"""Complete human case source replay and actual retained file ownership."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from escalation_team.human import HumanWorkspace
from escalation_team.source import parse
from escalation_team.strict import MAX_INTEGER, parse_json

FIXTURE = Path(__file__).parents[1] / "fixtures/starter.json"


def seed(tmp_path, value=None):
    path = tmp_path / "source.json"
    path.write_bytes(
        FIXTURE.read_bytes() if value is None else json.dumps(value).encode()
    )
    return path, HumanWorkspace(path)


def action(owner, kind, **args):
    case_id = args.pop("case_id", "C-901")
    row = next(r for r in owner.inspect()["business"]["cases"] if r["id"] == case_id)
    return {"kind": kind, "args": {"case_id": case_id, "source": row, **args}}


def test_human_full_mixed_handoff_credit_unlock_history_retains_source_snapshots(
    tmp_path,
):
    original = FIXTURE.read_bytes()
    path, owner = seed(tmp_path)
    with owner:
        other = deepcopy(owner.inspect()["business"]["cases"][:2])
        assert (
            owner.apply(
                action(owner, "handoff", case_id="C-903", actor="triage", to="billing")
            )["status"]
            == "handoff_applied"
        )
        assert (
            owner.apply(
                action(
                    owner,
                    "credit",
                    case_id="C-903",
                    expected_revision=2,
                    amount_cents=3500,
                )
            )["status"]
            == "credited"
        )
        owner.apply(
            action(owner, "handoff", case_id="C-903", actor="billing", to="account")
        )
        assert (
            owner.apply(action(owner, "unlock", case_id="C-903", expected_revision=4))[
                "status"
            ]
            == "unlocked"
        )
        current = owner.inspect()
        assert current["business"]["cases"][:2] == other
        row = current["business"]["cases"][2]
        assert (
            row["credit_issued"] and not row["account_locked"] and row["revision"] == 5
        )
        assert row["handoffs"] == ["billing", "account"]
        assert len(current["history"]) == len(current["business"]["audit"]) == 4
        assert current["business"]["audit"][1]["source"]["credit_issued"] is False
        assert current["origin"]["provenance"]["prior_model"] is None
        assert parse(path.read_bytes()) == current
    assert owner.closed and owner.fd is None
    assert FIXTURE.read_bytes() == original


def test_current_human_permissions_and_identity_invalidate_old_read_without_rollback(
    tmp_path,
):
    path, owner = seed(tmp_path)
    with owner:
        owner.apply(action(owner, "handoff", actor="triage", to="billing"))
        old = action(owner, "credit", expected_revision=2, amount_cents=4500)
        owner.apply(
            action(
                owner,
                "update",
                updates={
                    "customer": "Changed Current Customer",
                    "billing_write": False,
                },
            )
        )
        before = path.read_bytes()
        assert owner.apply(old)["status"] == "case_changed"
        assert (
            owner.apply(
                action(owner, "credit", expected_revision=3, amount_cents=4500)
            )["status"]
            == "permission_denied"
        )
        assert path.read_bytes() == before
        current = owner.inspect()
        assert [a["event"] for a in current["business"]["audit"]] == [
            "handoff",
            "case_updated",
        ]
        assert current["business"]["cases"][0]["credit_issued"] is False


def test_wrong_owner_revision_amount_and_repeated_credit_keep_literal_noop_bytes(
    tmp_path,
):
    path, owner = seed(tmp_path)
    with owner:
        assert (
            owner.apply(
                action(owner, "credit", expected_revision=1, amount_cents=4500)
            )["status"]
            == "wrong_owner"
        )
        owner.apply(action(owner, "handoff", actor="triage", to="billing"))
        before = path.read_bytes()
        for args, status in (
            ({"expected_revision": 1, "amount_cents": 4500}, "stale_revision"),
            ({"expected_revision": 2, "amount_cents": 1000}, "amount_not_supported"),
        ):
            assert owner.apply(action(owner, "credit", **args))["status"] == status
            assert path.read_bytes() == before
        owner.apply(action(owner, "credit", expected_revision=2, amount_cents=4500))
        before = path.read_bytes()
        assert (
            owner.apply(
                action(owner, "credit", expected_revision=3, amount_cents=4500)
            )["status"]
            == "already_credited"
        )
        assert path.read_bytes() == before


@pytest.mark.parametrize(
    "damage",
    [
        "string_permission",
        "bool_revision",
        "float_charge",
        "unsupported_credit",
        "unsupported_owner",
        "extra",
        "duplicate_case",
    ],
)
def test_complete_initial_case_refuses_unsupported_original_fields_and_effects(
    tmp_path, damage
):
    value = json.loads(FIXTURE.read_bytes())
    row = value["cases"][0]
    if damage == "string_permission":
        row["billing_write"] = "false"
    elif damage == "bool_revision":
        row["revision"] = True
    elif damage == "float_charge":
        row["charge_cents"] = 4500.0
    elif damage == "unsupported_credit":
        row["credit_issued"] = True
    elif damage == "unsupported_owner":
        row.update(owner="billing", handoffs=["billing"])
    elif damage == "extra":
        row["live_auth"] = True
    else:
        value["cases"].append(deepcopy(row))
    path = tmp_path / "source.json"
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        HumanWorkspace(path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "damage", ["business", "audit", "history", "snapshot", "result", "origin"]
)
def test_every_current_and_historical_effect_requires_complete_exact_replay(
    tmp_path, damage
):
    path, owner = seed(tmp_path)
    with owner:
        owner.apply(action(owner, "handoff", actor="triage", to="billing"))
        owner.apply(action(owner, "credit", expected_revision=2, amount_cents=4500))
        current = owner.inspect()
    if damage == "business":
        current["business"]["cases"][0]["customer"] = "Unsupported Customer"
    elif damage == "audit":
        current["business"]["audit"].pop()
    elif damage == "history":
        current["history"].pop(0)
    elif damage == "snapshot":
        current["history"][1]["action"]["args"]["source"]["billing_write"] = False
    elif damage == "result":
        current["history"][1]["result"]["credit_cents"] = 1
    else:
        current["origin"]["provenance"]["prior_model"] = "invented"
    raw = json.dumps(current).encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        HumanWorkspace(path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":-0}',
        b'{"a":1e400}',
        b'{"a":"\\ud800"}',
        b"\xff",
        b"[" * 66 + b"0" + b"]" * 66,
    ],
)
def test_original_json_ambiguity_refuses(raw):
    with pytest.raises((ValueError, UnicodeError)):
        parse_json(raw)


@pytest.mark.parametrize("revision", [MAX_INTEGER - 1, MAX_INTEGER])
def test_revision_exhaustion_preserves_last_successful_handoff(tmp_path, revision):
    value = json.loads(FIXTURE.read_bytes())
    value["baselineVersion"] = 1
    value["cases"][0]["revision"] = revision
    path, owner = seed(tmp_path, value)
    with owner:
        if revision < MAX_INTEGER:
            owner.apply(action(owner, "handoff", actor="triage", to="billing"))
            before = path.read_bytes()
            with pytest.raises(ValueError, match="revision_exhausted"):
                owner.apply(
                    action(
                        owner,
                        "credit",
                        expected_revision=MAX_INTEGER,
                        amount_cents=4500,
                    )
                )
        else:
            before = path.read_bytes()
            with pytest.raises(ValueError, match="revision_exhausted"):
                owner.apply(action(owner, "handoff", actor="triage", to="billing"))
        assert path.read_bytes() == before


@pytest.mark.parametrize("alias", ["symlink", "hardlink", "missing", "replaced"])
def test_owned_original_source_alias_replacement_missing_and_explicit_close(
    tmp_path, alias
):
    path, owner = seed(tmp_path)
    if alias in ("symlink", "hardlink"):
        other = tmp_path / "alias.json"
        if alias == "symlink":
            other.symlink_to(path)
        else:
            os.link(path, other)
        with pytest.raises((OSError, ValueError)):
            HumanWorkspace(other)
    elif alias == "missing":
        path.unlink()
        with pytest.raises(FileNotFoundError):
            owner.inspect()
        assert not path.exists()
    else:
        other = tmp_path / "replace.json"
        other.write_bytes(path.read_bytes())
        other.replace(path)
        with pytest.raises(ValueError):
            owner.inspect()
    owner.close()
    owner.close()
    assert owner.closed and owner.fd is None
    with pytest.raises(ValueError):
        owner.inspect()
