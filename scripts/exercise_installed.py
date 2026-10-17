"""Actual installed production CLIs/SDK HTTP and retained credits after late failure/signals."""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import escalation_team

BASE = Path(sys.argv[1])
BASE.mkdir()
assert (
    Path(escalation_team.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
)
if "--production-only" in sys.argv:
    assert importlib.util.find_spec("pytest") is None
BIN = Path(sys.executable).parent
ENV = {
    key: os.environ[key]
    for key in ("PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "SystemRoot", "WINDIR")
    if key in os.environ
}
KEY, MODEL = "synthetic-installed-customer-key", "gpt-owned-customer-installation"


def execute(command, expected=0, env=ENV, timeout=60):
    result = subprocess.run(
        list(map(str, command)), cwd=BASE, env=env, capture_output=True, timeout=timeout
    )
    assert result.returncode == expected, (result.stdout[-5000:], result.stderr[-5000:])
    return json.loads(result.stdout)


probe = execute(
    [
        sys.executable,
        "-I",
        Path(__file__).with_name("qualify_cases.py"),
        "--root",
        BASE / "populations",
    ],
    timeout=600,
)
assert probe["count"] == 12 and probe["model_requests"] == 118
launcher = BASE / "escalation-team"
launcher.symlink_to(BIN / "escalation-team")
proof = []
for mode in ("completed", "unfinished-after-credit", "SIGINT", "SIGTERM", "SIGHUP"):
    case = execute(
        [
            BIN / "escalation-team-cases",
            "init",
            "--case",
            "starter-billing",
            "--root",
            BASE / mode,
        ]
    )
    entered, release = threading.Event(), threading.Event()
    requests, errors = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                assert (
                    self.path == "/v1/responses"
                    and self.headers.get("authorization") == "Bearer " + KEY
                )
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                requests.append(body)
                n = len(requests)
                if n <= 4:
                    name = (
                        "get_case",
                        "transfer_to_billing_specialist",
                        "get_case",
                        "issue_credit",
                    )[n - 1]
                    arguments = {}
                    if n == 4:
                        outputs = [
                            json.loads(item["output"])
                            for item in body["input"]
                            if item.get("type") == "function_call_output"
                            and type(item.get("output")) is str
                        ]
                        row = next(
                            value["case"]
                            for value in reversed(outputs)
                            if "case" in value
                        )
                        arguments = {
                            "expected_revision": row["revision"],
                            "amount_cents": row["charge_cents"],
                        }
                    output = [
                        {
                            "type": "function_call",
                            "id": f"fc_install_{n}",
                            "call_id": f"call_install_{n}",
                            "name": name,
                            "arguments": json.dumps(arguments),
                            "status": "completed",
                        }
                    ]
                else:
                    if mode.startswith("SIG"):
                        entered.set()
                        assert release.wait(35)
                    output = [
                        {
                            "type": "message",
                            "id": "msg_install_final",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "The synthetic credit tool acknowledged the exact requested action.",
                                    "annotations": [],
                                }
                            ],
                        }
                    ]
                value = {
                    "id": f"resp_install_{n}",
                    "object": "response",
                    "created_at": 1792195200,
                    "status": (
                        None
                        if mode == "unfinished-after-credit" and n == 5
                        else "completed"
                    ),
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
                    "original_unknown": {"retained": True},
                }
                raw = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except BaseException as error:
                errors.append(str(error))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    child = subprocess.Popen(
        list(
            map(
                str,
                [
                    launcher,
                    "--db",
                    case["database"],
                    "--case",
                    "C-901",
                    "--request",
                    "Issue only this exact verified duplicate credit if currently authorized.",
                    "--credit-cents",
                    "4500",
                    "--allow-writes",
                    "--model",
                    MODEL,
                    "--base-url",
                    f"http://127.0.0.1:{server.server_port}",
                    "--output",
                    BASE / mode / "receipts",
                ],
            )
        ),
        cwd=BASE,
        env={**ENV, "OPENAI_API_KEY": KEY},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        if mode.startswith("SIG"):
            assert entered.wait(20)
            child.send_signal(getattr(signal, mode))
        stdout, stderr = child.communicate(timeout=30)
        assert child.returncode == (0 if mode == "completed" else 1), (
            stdout[-5000:],
            stderr[-5000:],
        )
        result = json.loads(stdout)
        receipt_path = Path(result["receipt_path"])
        receipt = json.loads(receipt_path.read_text())
        assert (
            receipt["cleanup_confirmed"]
            and len(receipt["acknowledged_effects"]) == 2
            and len(requests) == 5
        )
        assert (
            receipt["observed_business"]["credit_complete"]
            and receipt["observed_business"]["workflow_complete"]
        )
        assert KEY not in json.dumps(receipt)
        final = execute(
            [BIN / "escalation-team-cases", "inspect", "--root", case["root"]]
        )
        assert final["source"]["business"]["cases"][0]["credit_issued"]
        assert all(
            o["status"] == "closed" for o in final["source"]["records"]["operations"]
        )
        assert [a["event"] for a in final["source"]["business"]["audit"]] == [
            "handoff",
            "credit_issued",
        ]
        assert receipt["status"] == ("completed" if mode == "completed" else "failed")
        wire = json.dumps(requests)
        for private in (
            case["case_id"],
            case["instance_id"],
            case["seed_sha256"],
            case["root"],
            "fixture_base64",
            "definition_sha256",
        ):
            assert private not in wire
        source_bytes, evidence_bytes = (
            Path(case["database"]).read_bytes(),
            receipt_path.read_bytes(),
        )
        fresh = execute(
            [
                BIN / "escalation-team-cases",
                "reset",
                "--root",
                case["root"],
                "--new-root",
                BASE / (mode + "-reset"),
            ]
        )
        assert fresh["instance_id"] != case["instance_id"] and fresh["generation"] == 2
        assert fresh["source"]["business"] == case["source"]["business"]
        assert (
            Path(case["database"]).read_bytes() == source_bytes
            and receipt_path.read_bytes() == evidence_bytes
        )
        proof.append(
            {
                "mode": mode,
                "requests": 5,
                "acknowledged_effects": 2,
                "credit_retained": True,
                "cleanup_confirmed": True,
                "preserving_reset": True,
            }
        )
    finally:
        release.set()
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive() and errors == [], errors

default = execute([BIN / "escalation-team-state", "init", "--db", BASE / "default.db"])
assert (
    len(default["business"]["cases"]) == 3
    and default["source"]["creation"] == "fixture"
)
missing = execute(
    [
        launcher,
        "--db",
        BASE / "absent.db",
        "--case",
        "C-901",
        "--request",
        "Inspect only.",
        "--model",
        MODEL,
        "--output",
        BASE / "missing-key",
    ],
    expected=1,
)
assert not (BASE / "absent.db").exists()
missing_receipt = json.loads(Path(missing["receipt_path"]).read_text())
assert missing_receipt["allocation"] == {
    "source": False,
    "scope": False,
    "provider": False,
}
print(
    json.dumps(
        {
            "cases": 12,
            "case_model_requests": probe["model_requests"],
            "normal_sdk_cli": proof,
            "default_seed": True,
            "missing_key_before_source": True,
            "production_only": "--production-only" in sys.argv,
            "real_provider_or_external_effect": False,
        }
    )
)
