"""Deterministically author fictional support case history through the actual human seam."""

import argparse
import base64
import json
import tempfile
from copy import deepcopy
from pathlib import Path

from escalation_team.human import HumanWorkspace
from escalation_team.source import PROVENANCE, parse
from escalation_team.strict import MAX_INTEGER, sha256

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src/escalation_team/data"


def encoded(value):
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    ).encode()


def change(owner, ident, kind, **args):
    row = next(r for r in owner.inspect()["business"]["cases"] if r["id"] == ident)
    return owner.apply(
        {"kind": kind, "args": {"case_id": ident, "source": row, **args}}
    )


def busy():
    first = ("Avery", "Jordan", "Morgan", "Casey", "Riley", "Taylor", "Jamie", "Parker")
    last = ("Rivera", "Okafor", "Laurent", "Sato", "Bennett", "Ibrahim")
    cases = []
    for n in range(48):
        group = n // 8
        billing, account = group in (0, 2, 3, 5), group in (1, 2, 4, 5)
        cents = 3500 if group in (2, 5) else 2500 + 200 * (n % 8) if billing else 0
        issue = (
            "Duplicate charge and locked account"
            if billing and account
            else (
                "Verified duplicate charge"
                if billing
                else "Account locked after access reset"
            )
        )
        cases.append(
            {
                "id": f"C-{1001+n}",
                "customer": f"{first[n%8]} {last[group]}",
                "request": f"{issue} for workspace {201+n}.",
                "verified": True,
                "charge_cents": cents,
                "credit_issued": False,
                "account_locked": account,
                "billing_write": billing,
                "account_write": account,
                "owner": "triage",
                "revision": 1,
                "handoffs": [],
            }
        )
    with tempfile.TemporaryDirectory(prefix="customer-author-") as directory:
        path = Path(directory) / "fixture.json"
        path.write_bytes(encoded({"cases": cases, "audit": []}))
        with HumanWorkspace(path) as owner:
            for row in cases:
                change(
                    owner,
                    row["id"],
                    "update",
                    updates={
                        "request": row["request"]
                        + " Customer confirmed the workspace details."
                    },
                )
            for n in range(24, 48):
                row = cases[n]
                role = "account" if n in range(32, 40) else "billing"
                change(owner, row["id"], "handoff", actor="triage", to=role)
                if n < 32:
                    change(
                        owner,
                        row["id"],
                        "credit",
                        expected_revision=3,
                        amount_cents=row["charge_cents"],
                    )
                elif n < 40:
                    change(owner, row["id"], "unlock", expected_revision=3)
                else:
                    change(
                        owner,
                        row["id"],
                        "update",
                        updates={"billing_write": False, "account_write": False},
                    )
        return path.read_bytes()


def run(case_id, credit=None, unlock=False):
    request = "Resolve only the exact requested synthetic support actions if current verification, ownership and permissions allow."
    return {
        "case_id": case_id,
        "request": request,
        "credit_cents": credit,
        "unlock": unlock,
    }


def case(
    ident,
    profile,
    title,
    raw,
    runs,
    statuses,
    workflows,
    calls,
    *,
    during=None,
    native_fault=None,
):
    source = parse(raw)
    return {
        "id": ident,
        "profile": profile,
        "title": title,
        "fixture_base64": base64.b64encode(raw).decode(),
        "fixture_sha256": sha256(raw),
        "runs": runs,
        "during": during,
        "native_fault": native_fault,
        "reference": {
            "technical_statuses": statuses,
            "workflow_complete": workflows,
            "model_requests": calls,
            "prior_cases": len(source["business"]["cases"]),
            "prior_audit": len(source["business"]["audit"]),
            "prior_human_steps": len(source["history"]),
            "prior_model": None,
            "authentication": None,
            "actual_external_effect": None,
        },
    }


def build():
    starter = (ROOT / "fixtures/starter.json").read_bytes()
    loaded = busy()
    cap = json.loads(starter)
    cap["cases"][0]["charge_cents"] = 5000
    over = deepcopy(cap)
    over["cases"][0]["charge_cents"] = 5001
    maximum = json.loads(starter)
    maximum["baselineVersion"] = 1
    maximum["cases"][0]["revision"] = MAX_INTEGER - 1
    cases = [
        case(
            "starter-billing",
            "starter",
            "Verified duplicate charge",
            starter,
            [run("C-901", 4500)],
            ["completed"],
            [True],
            5,
        ),
        case(
            "starter-account",
            "starter",
            "Verified locked account",
            starter,
            [run("C-902", unlock=True)],
            ["completed"],
            [True],
            5,
        ),
        case(
            "starter-mixed",
            "starter",
            "Billing and account handoff",
            starter,
            [run("C-903", 3500, True)],
            ["completed"],
            [True],
            9,
        ),
        case(
            "busy-billing",
            "busy",
            "Four fresh credits amid earlier support work",
            loaded,
            [run(f"C-{1001+n}", 2500 + 200 * n) for n in range(4)],
            ["completed"] * 4,
            [True] * 4,
            20,
        ),
        case(
            "busy-mixed",
            "busy",
            "Four mixed cases amid credits, unlocks and revoked permissions",
            loaded,
            [run(f"C-{1017+n}", 3500, True) for n in range(4)],
            ["completed"] * 4,
            [True] * 4,
            36,
        ),
        case(
            "busy-prior",
            "busy",
            "Preserve four prior credits without duplicating them",
            loaded,
            [run(f"C-{1025+n}", 2500 + 200 * n) for n in range(4)],
            ["completed"] * 4,
            [True] * 4,
            20,
        ),
        case(
            "ambiguous-permission",
            "ambiguous",
            "Permission revoked before specialist inspection",
            starter,
            [run("C-901", 4500)],
            ["completed"],
            [False],
            5,
            during={
                "model_request": 3,
                "case_id": "C-901",
                "updates": {"billing_write": False},
            },
        ),
        case(
            "ambiguous-identity",
            "ambiguous",
            "Identity changes after specialist inspection",
            starter,
            [run("C-901", 4500)],
            ["failed"],
            [False],
            4,
            during={
                "model_request": 4,
                "case_id": "C-901",
                "updates": {
                    "customer": "Changed Current Customer",
                    "billing_write": False,
                },
            },
        ),
        case(
            "ambiguous-final",
            "ambiguous",
            "Incomplete final after a real credit",
            starter,
            [run("C-901", 4500)],
            ["failed"],
            [True],
            5,
            native_fault={"model_request": 5, "kind": "incomplete_final"},
        ),
        case(
            "boundary-credit-cap",
            "boundary",
            "Exact maximum supported credit",
            encoded(cap),
            [run("C-901", 5000)],
            ["completed"],
            [True],
            5,
        ),
        case(
            "boundary-over-cap",
            "boundary",
            "Unsupported caller credit refuses before model allocation",
            encoded(over),
            [run("C-901", 5001)],
            ["failed"],
            [False],
            0,
        ),
        case(
            "boundary-revision",
            "boundary",
            "Last safe ownership handoff before credit exhaustion",
            encoded(maximum),
            [run("C-901", 4500)],
            ["failed"],
            [False],
            4,
        ),
    ]
    return starter, encoded(
        {
            "id": "customer-escalation-populations",
            "version": "1.0.0",
            "synthetic": True,
            "provenance": PROVENANCE,
            "cases": cases,
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    starter, populations = build()
    manifest = f'"""Literal immutable installed population pins; regenerate with authoring script."""\n\nSTARTER_BYTES = {len(starter)}\nSTARTER_SHA256 = "{sha256(starter)}"\nDATASET_BYTES = {len(populations)}\nDATASET_SHA256 = "{sha256(populations)}"\nDATASET_CASES = 12\n'.encode()
    for path, raw in (
        (DATA / "starter.json", starter),
        (DATA / "populations.json", populations),
        (DATA.parent / "_dataset_manifest.py", manifest),
    ):
        if args.check:
            if path.read_bytes() != raw:
                raise ValueError(f"authored population differs: {path.name}")
        else:
            path.write_bytes(raw)
    print(
        json.dumps(
            {
                "cases": 12,
                "bytes": len(populations),
                "sha256": sha256(populations),
                "mode": "checked" if args.check else "authored",
            }
        )
    )


if __name__ == "__main__":
    main()
