"""Actual owned SQLite, exact current caller/case/role/read and atomic decisions."""

import json
import os
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from escalation_team.managed import Credential, ManagedSource
from escalation_team.strict import canonical, digest

FIXTURE = Path(__file__).parents[1] / "fixtures/starter.json"


def seed(tmp_path):
    return ManagedSource.seed(tmp_path / "case.db", FIXTURE)


def scope(source, *, write=True, case_id="C-903", credit=3500, unlock=True):
    intent = source.intent(
        case_id,
        "Resolve only the exact requested support actions.",
        credit_cents=credit,
        unlock=unlock,
    )
    return source.begin(intent, "Actual owned SDK case workflow", allow_writes=write)


def apply(source, credential, role, kind, read=None, **args):
    read = source.observe(credential, role) if read is None else read
    row = read["value"]["case"]
    action = {
        "kind": kind,
        "args": {"case_id": row["id"], "source": deepcopy(row), **args},
    }
    return source.commit(
        credential, role, action, read["observation"]["id"], read["proof"]
    )


def test_actual_full_owned_mixed_case_handoff_credit_unlock_replay_and_close(tmp_path):
    source = seed(tmp_path)
    before = source.snapshot()
    cap = scope(source)
    with source:
        assert (
            apply(source, cap, "triage", "handoff", actor="triage", to="billing")[
                "result"
            ]["status"]
            == "handoff_applied"
        )
        assert (
            apply(
                source, cap, "billing", "credit", expected_revision=2, amount_cents=3500
            )["result"]["status"]
            == "credited"
        )
        apply(source, cap, "billing", "handoff", actor="billing", to="account")
        assert (
            apply(source, cap, "account", "unlock", expected_revision=4)["result"][
                "status"
            ]
            == "unlocked"
        )
        final = source.snapshot()
        assert final["business"]["cases"][:2] == before["business"]["cases"][:2]
        assert final["roles"][cap.id] == "account"
        assert [d["status"] for d in final["records"]["decisions"]] == ["applied"] * 4
        source.close_operation(cap)
        assert source.close_operation(cap)["already_closed"]
        with pytest.raises(ValueError):
            source.observe(cap, "account")
    assert source.database_closed and source.descriptor_closed
    with ManagedSource(source.path) as fresh:
        assert fresh.snapshot()["business"] == final["business"]
        assert fresh.snapshot()["records"]["operations"][0]["status"] == "closed"


def test_readonly_sdk_route_control_and_denied_writes_preserve_literal_business(
    tmp_path,
):
    with seed(tmp_path) as source:
        original = source.database.execute("SELECT document FROM business").fetchone()[
            0
        ]
        cap = scope(source, write=False)
        assert (
            apply(source, cap, "triage", "handoff", actor="triage", to="billing")[
                "decision"
            ]["status"]
            == "denied"
        )
        assert source.snapshot()["roles"][cap.id] == "billing"
        result = apply(
            source, cap, "billing", "credit", expected_revision=1, amount_cents=3500
        )
        assert result["result"]["status"] == "write_permission_required"
        assert (
            source.database.execute("SELECT document FROM business").fetchone()[0]
            == original
        )
        assert source.snapshot()["business"]["audit"] == []
        source.close_operation(cap)


def test_current_permission_update_after_handoff_invalidates_old_read_then_denies_credit(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source)
        apply(source, cap, "triage", "handoff", actor="triage", to="billing")
        old = source.observe(cap, "billing")
        with ManagedSource(source.path) as human:
            human.update(
                "C-903",
                {"billing_write": False, "customer": "Changed Current Customer"},
            )
        with pytest.raises(ValueError, match="current"):
            apply(
                source,
                cap,
                "billing",
                "credit",
                read=old,
                expected_revision=2,
                amount_cents=3500,
            )
        result = apply(
            source, cap, "billing", "credit", expected_revision=3, amount_cents=3500
        )
        assert result["result"]["status"] == "permission_denied"
        assert [a["event"] for a in source.snapshot()["business"]["audit"]] == [
            "handoff",
            "case_updated",
        ]
        source.close_operation(cap)


def test_caller_exact_action_intent_separate_from_model_permission_and_current_source(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source, credit=None, unlock=False)
        apply(source, cap, "triage", "handoff", actor="triage", to="billing")
        assert (
            apply(
                source, cap, "billing", "credit", expected_revision=2, amount_cents=3500
            )["result"]["status"]
            == "caller_intent_required"
        )
        apply(source, cap, "billing", "handoff", actor="billing", to="account")
        assert (
            apply(source, cap, "account", "unlock", expected_revision=3)["result"][
                "status"
            ]
            == "caller_intent_required"
        )
        row = source.snapshot()["business"]["cases"][2]
        assert not row["credit_issued"] and row["account_locked"]
        source.close_operation(cap)
        for amount in (True, "3500", 3500.0, 1, 5001):
            with pytest.raises(ValueError):
                source.intent("C-903", "Credit", credit_cents=amount)


@pytest.mark.parametrize(
    "fault", ["guessed", "foreign", "role", "case", "consumed", "closed", "route"]
)
def test_guessed_borrowed_wrong_role_case_route_used_or_closed_authority_rejects(
    tmp_path, fault
):
    with seed(tmp_path) as source:
        cap = scope(source)
        read = source.observe(cap, "triage")
        action = {
            "kind": "handoff",
            "args": {
                "case_id": "C-903",
                "source": deepcopy(read["value"]["case"]),
                "actor": "triage",
                "to": "billing",
            },
        }
        credential, role, proof = cap, "triage", read["proof"]
        if fault == "guessed":
            proof = "guessed"
        elif fault == "foreign":
            credential = Credential(cap.id, "wrong", cap.authority_sha256)
        elif fault == "role":
            role = "billing"
        elif fault == "case":
            action["args"]["case_id"] = "C-901"
        elif fault == "route":
            action["args"]["to"] = "triage"
        elif fault == "consumed":
            source.commit(cap, role, action, read["observation"]["id"], proof)
        else:
            source.close_operation(cap)
        before = source.snapshot()["sha256"]
        with pytest.raises(ValueError):
            source.commit(credential, role, action, read["observation"]["id"], proof)
        assert source.snapshot()["sha256"] == before
        source.close_operation(cap)


def test_no_role_borrowing_after_real_handoff_and_fresh_specialist_read_required(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source)
        apply(source, cap, "triage", "handoff", actor="triage", to="billing")
        old = source.observe(cap, "billing")
        apply(source, cap, "billing", "handoff", actor="billing", to="account")
        with pytest.raises(ValueError):
            source.observe(cap, "billing")
        with pytest.raises(ValueError):
            apply(
                source,
                cap,
                "billing",
                "credit",
                read=old,
                expected_revision=2,
                amount_cents=3500,
            )
        read = source.observe(cap, "account")
        with pytest.raises(ValueError):
            apply(
                source,
                cap,
                "account",
                "credit",
                read=read,
                expected_revision=3,
                amount_cents=3500,
            )
        assert (
            apply(source, cap, "account", "unlock", read=read, expected_revision=3)[
                "result"
            ]["status"]
            == "unlocked"
        )
        source.close_operation(cap)


def test_actual_sql_self_consistent_permission_change_cannot_promote_live_caller(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source, write=False)
        row = source.database.execute("SELECT document FROM operations").fetchone()[0]
        value = json.loads(row)
        value["allow_writes"] = True
        source.database.execute("UPDATE operations SET document=?", (canonical(value),))
        assert source.snapshot()["records"]["operations"][0]["allow_writes"]
        with pytest.raises(ValueError):
            source.observe(cap, "triage")
        assert source.close_operation(cap)["closed"]


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "header",
        "business",
        "raw_duplicate",
        "read_value",
        "decision_result",
        "closure_cutoff",
    ],
)
def test_exact_original_sql_header_tables_observations_and_decisions_refuse_without_repair(
    tmp_path, fault
):
    source = seed(tmp_path)
    cap = scope(source)
    apply(source, cap, "triage", "handoff", actor="triage", to="billing")
    source.close_operation(cap)
    source.close()
    with sqlite3.connect(source.path) as db:
        if fault == "schema":
            db.execute("CREATE TABLE injected(x)")
        elif fault == "raw_duplicate":
            db.execute(
                'UPDATE business SET document=\' {"cases":[],"audit":[],"audit":[]}\''
            )
        else:
            table = (
                "metadata"
                if fault == "header"
                else (
                    "business"
                    if fault == "business"
                    else (
                        "observations"
                        if fault == "read_value"
                        else "decisions" if fault == "decision_result" else "operations"
                    )
                )
            )
            rowid, raw = db.execute(
                "SELECT id,document FROM " + table + " LIMIT 1"
            ).fetchone()
            value = json.loads(raw)
            if fault == "header":
                value["fixture_sha256"] = "0" * 64
            elif fault == "business":
                value["cases"][0]["customer"] = "Unsupported Change"
            elif fault == "read_value":
                value["value"]["case"]["verified"] = False
            elif fault == "decision_result":
                value["result"]["owner"] = "account"
            else:
                value["closed_frontier"] = 0
            db.execute(
                "UPDATE " + table + " SET document=? WHERE id=?",
                (canonical(value), rowid),
            )
    before = source.path.read_bytes()
    with pytest.raises(ValueError):
        ManagedSource(source.path)
    assert source.path.read_bytes() == before


def test_cancelled_precommit_rolls_back_and_control_closes_even_with_damaged_business(
    tmp_path,
):
    with seed(tmp_path) as source:
        cap = scope(source)
        read = source.observe(cap, "triage")
        before = source.snapshot()["sha256"]

        def cancelled():
            raise TimeoutError("owned deadline")

        source.commit_guard = cancelled
        with pytest.raises(TimeoutError):
            apply(
                source,
                cap,
                "triage",
                "handoff",
                read=read,
                actor="triage",
                to="billing",
            )
        assert source.snapshot()["sha256"] == before
        source.database.execute("UPDATE business SET document='{}'")
        with pytest.raises(ValueError):
            source.snapshot()
        assert source.close_operation(cap)["closed"]
        operation = json.loads(
            source.database.execute("SELECT document FROM operations").fetchone()[0]
        )
        assert operation["status"] == "closed"


def test_real_writer_lock_and_second_owner_refuse_before_scope_and_existing_seed_is_exclusive(
    tmp_path,
):
    with seed(tmp_path) as source:
        with sqlite3.connect(source.path, isolation_level=None) as writer:
            writer.execute("BEGIN IMMEDIATE")
            with pytest.raises(sqlite3.OperationalError):
                scope(source)
            writer.execute("ROLLBACK")
        assert source.snapshot()["records"]["operations"] == []
        cap = scope(source)
        with ManagedSource(source.path) as other:
            with pytest.raises(ValueError, match="active"):
                scope(other)
        with pytest.raises(FileExistsError):
            ManagedSource.seed(source.path, FIXTURE)
        source.close_operation(cap)
    absent = tmp_path / "absent.db"
    with pytest.raises(FileNotFoundError):
        ManagedSource(absent)
    assert not absent.exists()


def test_actual_connection_and_descriptor_close_once_with_failed_acknowledgement(
    tmp_path,
):
    source = seed(tmp_path)
    real = source.database

    class ClosedThenFailed:
        calls = 0

        def __getattr__(self, name):
            return getattr(real, name)

        def close(self):
            self.calls += 1
            real.close()
            raise RuntimeError("close acknowledgement lost after actual close")

    wrapper = ClosedThenFailed()
    source.database = wrapper
    result = source.close()
    assert not result["database_closed"] and result["descriptor_closed"]
    assert result["errors"][0]["resource"] == "database"
    source.close()
    assert wrapper.calls == 1
    with pytest.raises(sqlite3.ProgrammingError):
        real.execute("SELECT 1")


def test_private_actual_cli_outside_checkout_model_free_inspect_and_exact_intent(
    tmp_path,
):
    def run(*args):
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "escalation_team.managed_cli",
                *map(str, args),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    database = tmp_path / "private.db"
    initial = run("init", "--db", database, "--fixture", FIXTURE)
    assert initial["records"] == {"operations": [], "observations": [], "decisions": []}
    intent = run(
        "intent",
        "--db",
        database,
        "--case",
        "C-903",
        "--request",
        "Resolve exact requested actions.",
        "--credit-cents",
        "3500",
        "--unlock",
    )
    assert intent["credit_cents"] == 3500 and intent["unlock"] is True
    run(
        "update",
        "--db",
        database,
        "--case",
        "C-903",
        "--updates-json",
        '{"billing_write":false}',
    )
    final = run("inspect", "--db", database)
    assert final["source"]["instance_id"] == initial["source"]["instance_id"]
    assert final["business"]["cases"][2]["billing_write"] is False
    assert (
        final["records"]["operations"] == [] and final["records"]["observations"] == []
    )
