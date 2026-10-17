"""Installed private cases/reset with actual normal SDKs and owned Responses HTTP."""

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from escalation_team.domain import selected
from escalation_team.managed import ManagedSource
from escalation_team.owned_runtime import observed, run_owned_team
from escalation_team.populations import definition
from escalation_team.strict import sha256

KEY, MODEL = "synthetic-owned-customer-case-key", "gpt-owned-population-probe"


def cli(*args):
    result = subprocess.run(
        [sys.executable, "-I", "-m", "escalation_team.cases", *map(str, args)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        cwd=tempfile.gettempdir(),
    )
    return json.loads(result.stdout)


def files(root):
    return {
        str(p.relative_to(root)): (p.stat().st_mode & 0o777, sha256(p.read_bytes()))
        for p in root.rglob("*")
        if p.is_file()
    }


def next_call(body):
    public = json.loads(body["input"][0]["content"])
    names = {tool["name"] for tool in body["tools"]}
    role = (
        "billing"
        if "issue_credit" in names
        else "account" if "unlock_account" in names else "triage"
    )
    items = body["input"]
    if role != "triage":
        hops = [
            i
            for i, item in enumerate(items)
            if item.get("type") == "function_call"
            and item.get("name") == "transfer_to_" + role + "_specialist"
        ]
        assert hops
        items = items[hops[-1] + 1 :]
    calls = [item for item in items if item.get("type") == "function_call"]
    values = [
        json.loads(item["output"])
        for item in items
        if item.get("type") == "function_call_output"
        and type(item.get("output")) is str
    ]
    rows = [v["case"] for v in values if type(v) is dict and "case" in v]
    if not rows:
        return "get_case", {}
    row = rows[-1]
    if role == "triage":
        return (
            ("transfer_to_billing_specialist", {})
            if public["requested_credit_cents"] is not None
            else (
                ("transfer_to_account_specialist", {})
                if public["requested_unlock"]
                else (None, {})
            )
        )
    if role == "billing":
        if not any(c["name"] == "issue_credit" for c in calls):
            return "issue_credit", {
                "expected_revision": row["revision"],
                "amount_cents": public["requested_credit_cents"],
            }
        if public["requested_unlock"]:
            return (
                ("get_case", {})
                if calls[-1]["name"] != "get_case"
                else ("transfer_to_account_specialist", {})
            )
    elif not any(c["name"] == "unlock_account" for c in calls):
        return "unlock_account", {"expected_revision": row["revision"]}
    return None, {}


@contextmanager
def endpoint(database, case):
    requests, errors = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                assert self.path == "/v1/responses"
                assert self.headers.get("authorization") == "Bearer " + KEY
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                requests.append(body)
                n = len(requests)
                # Synthesize protocol responses only from actual public SDK data.
                # Private current-event injection is a separate human source actor.
                name, arguments = next_call(body)
                if name is not None:
                    output = [
                        {
                            "type": "function_call",
                            "id": f"fc_case_{n}",
                            "call_id": f"call_case_{n}",
                            "name": name,
                            "arguments": json.dumps(arguments),
                            "status": "completed",
                        }
                    ]
                else:
                    output = [
                        {
                            "type": "message",
                            "id": f"msg_case_{n}",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "The tools reported the current synthetic case state. Inspect the committed results for each requested action.",
                                    "annotations": [],
                                }
                            ],
                        }
                    ]
                value = {
                    "id": f"resp_case_{n}",
                    "object": "response",
                    "created_at": 1792195200,
                    "status": "completed",
                    "error": None,
                    "incomplete_details": None,
                    "model": MODEL,
                    "output": output,
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": body["tools"],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "total_tokens": 12,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                    "original_unknown": {"owned_http": True},
                }
                during = case["during"]
                if during is not None and during["model_request"] == n:
                    with ManagedSource(database) as human:
                        result = human.update(during["case_id"], during["updates"])
                        assert result["result"]["status"] == "updated"
                fault = case["native_fault"]
                if fault is not None and fault["model_request"] == n:
                    assert fault["kind"] == "incomplete_final"
                    value["output"][0]["status"] = "incomplete"
                raw = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except BaseException as error:
                errors.append(repr(error))
                self.send_response(500)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, errors
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def qualify(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    listed = cli("list")
    assert len(listed["cases"]) == 12
    results = []
    for item in listed["cases"]:
        case = definition(item["id"])
        original = root / case["id"]
        initial = cli("init", "--case", case["id"], "--root", original)
        assert (
            cli("inspect", "--root", original)["source_sha256"]
            == initial["source_sha256"]
        )
        assert all(
            initial["source"]["records"][key] == []
            for key in ("operations", "observations", "decisions")
        )
        statuses, workflows = [], []
        with endpoint(initial["database"], case) as (base, requests, errors):
            for caller in case["runs"]:
                try:
                    result = asyncio.run(
                        run_owned_team(
                            database=initial["database"],
                            **caller,
                            allow_writes=True,
                            api_key=KEY,
                            model=MODEL,
                            base_url=base,
                            output_root=str(original / "receipts"),
                            timeout_ms=120000,
                        )
                    )
                    statuses.append(result["status"])
                    receipt = json.loads(Path(result["receipt_path"]).read_text())
                except Exception as error:
                    receipt = error.evidence
                    assert receipt["status"] == "failed"
                    assert json.loads(json.dumps(receipt)) == json.loads(
                        Path(error.receipt_path).read_text()
                    )
                    statuses.append("failed")
                assert (
                    receipt["cleanup_confirmed"]
                    and receipt["recording_status"] == "durable"
                )
                with ManagedSource(initial["database"]) as source:
                    state = source.snapshot()
                baseline = selected(initial["source"]["business"], caller["case_id"])
                measured = observed(state, {**caller, "case": baseline})
                workflows.append(measured["workflow_complete"])
            reference = case["reference"]
            assert statuses == reference["technical_statuses"], (case["id"], statuses)
            assert workflows == reference["workflow_complete"], (case["id"], workflows)
            assert len(requests) == reference["model_requests"], (
                case["id"],
                len(requests),
            )
            assert errors == [], errors
            wire = json.dumps(requests)
            for private in (
                case["id"],
                "customer-escalation-populations",
                initial["instance_id"],
                initial["seed_sha256"],
                "definition_sha256",
                "dataset_sha256",
                "token_sha256",
                "private_reference",
                "prior_human_steps",
            ):
                assert private not in wire, private
            final = cli("inspect", "--root", original)
            assert all(
                o["status"] == "closed"
                for o in final["source"]["records"]["operations"]
            )
            targets = {r["case_id"] for r in case["runs"]}
            for old in initial["source"]["business"]["cases"]:
                if old["id"] not in targets:
                    assert old in final["source"]["business"]["cases"]
            assert (
                final["source"]["business"]["audit"][: initial["metrics"]["audit"]]
                == initial["source"]["business"]["audit"]
            )
            credits = [
                r["case_id"]
                for r in final["source"]["business"]["audit"]
                if r["event"] == "credit_issued"
            ]
            assert len(credits) == len(set(credits))
            before_files = files(original)
            reset = cli(
                "reset",
                "--root",
                original,
                "--new-root",
                root / (case["id"] + "-reset"),
            )
            assert files(original) == before_files
            assert (
                reset["instance_id"] != initial["instance_id"]
                and reset["seed_sha256"] != initial["seed_sha256"]
            )
            assert (
                reset["generation"] == 2
                and reset["reset_of"]["source_sha256"] == final["source_sha256"]
            )
            assert reset["source"]["business"] == initial["source"]["business"]
            assert all(
                reset["source"]["records"][key] == []
                for key in ("operations", "observations", "decisions")
            )
            results.append(
                {
                    "id": case["id"],
                    "statuses": statuses,
                    "workflow_complete": workflows,
                    "model_requests": len(requests),
                    "prior_metrics": initial["metrics"],
                    "preserving_reset": True,
                }
            )
    return {
        "cases": results,
        "count": len(results),
        "model_requests": sum(c["model_requests"] for c in results),
        "execution": "actual pinned SDKs and owned HTTP; explicit synthetic human updates",
        "real_provider_or_external_effect": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root")
    parser.add_argument("--report")
    args = parser.parse_args()
    if args.root:
        report = qualify(args.root)
    else:
        with tempfile.TemporaryDirectory(prefix="customer-installed-cases-") as root:
            report = qualify(root)
    raw = json.dumps(report, indent=2) + "\n"
    if args.report:
        Path(args.report).write_text(raw)
    print(raw)


if __name__ == "__main__":
    main()
