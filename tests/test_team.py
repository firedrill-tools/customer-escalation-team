import json
import shutil
from pathlib import Path

from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from escalation_team.team import make_team, run_team
from escalation_team.workspace import Workspace


FIXTURE = Path(__file__).parents[1] / "fixtures" / "starter.json"


def seeded(tmp_path):
    file = tmp_path / "workspace.json"
    shutil.copyfile(FIXTURE, file)
    return Workspace(file)


def test_billing_handoff_runs_sdk_and_retains_exact_shared_case(tmp_path):
    workspace = seeded(tmp_path)
    model = ScriptedModel([
        [function_call("get_case", {}, call_id="read-1")],
        [function_call("transfer_to_billing_specialist", {}, call_id="handoff-1")],
        [function_call("get_case", {}, call_id="read-2")],
        [function_call("issue_credit", {"expected_revision": 2, "amount_cents": 4500}, call_id="credit-1")],
        [assistant_message("C-901 credit was issued after billing review.")],
    ])
    result = run_team(workspace, "C-901", "Resolve the duplicate 45.00 CAD charge", model, True)
    model.assert_complete()
    assert result["last_agent"] == "Billing Specialist"
    assert result["case"]["credit_issued"] is True
    assert result["case"]["handoffs"] == ["billing"]
    assert [item["event"] for item in result["audit"]] == ["handoff", "credit_issued"]
    assert workspace.get("C-902")["revision"] == 1


def test_mixed_case_handoffs_between_specialists_without_losing_context(tmp_path):
    workspace = seeded(tmp_path)
    model = ScriptedModel([
        [function_call("transfer_to_billing_specialist", {}, call_id="handoff-1")],
        [function_call("get_case", {}, call_id="read-1")],
        [function_call("issue_credit", {"expected_revision": 2, "amount_cents": 3500}, call_id="credit-1")],
        [function_call("transfer_to_account_specialist", {}, call_id="handoff-2")],
        [function_call("get_case", {}, call_id="read-2")],
        [function_call("unlock_account", {"expected_revision": 4}, call_id="unlock-1")],
        [assistant_message("C-903 billing and account actions are complete.")],
    ])
    result = run_team(workspace, "C-903", "Handle the duplicate charge and locked account", model, True)
    model.assert_complete()
    assert result["last_agent"] == "Account Specialist"
    assert result["case"]["handoffs"] == ["billing", "account"]
    assert result["case"]["credit_issued"] is True
    assert result["case"]["account_locked"] is False
    assert result["case"]["revision"] == 5


def test_permission_change_after_handoff_blocks_billing_write(tmp_path):
    workspace = seeded(tmp_path)
    def revoke(_call):
        workspace.change_permissions("C-901", billing_write=False)
        return [function_call("get_case", {}, call_id="read-after-revoke")]
    model = ScriptedModel([
        [function_call("transfer_to_billing_specialist", {}, call_id="handoff-1")],
        ModelStep.respond(revoke),
        [function_call("issue_credit", {"expected_revision": 3, "amount_cents": 4500}, call_id="credit-1")],
        [assistant_message("Billing permission was revoked; no credit was issued.")],
    ])
    result = run_team(workspace, "C-901", "Credit the duplicate charge if permitted", model, True)
    model.assert_complete()
    assert result["case"]["credit_issued"] is False
    assert result["case"]["billing_write"] is False
    assert [item["event"] for item in result["audit"]] == ["handoff"]


def test_write_gate_stale_revision_and_wrong_owner(tmp_path):
    workspace = seeded(tmp_path)
    assert workspace.credit("C-901", 1, 4500)["error"] == "wrong_owner"
    workspace.assign("C-901", "billing")
    assert workspace.credit("C-901", 1, 4500)["error"] == "stale_revision"
    assert workspace.credit("C-901", 2, 1000)["error"] == "amount_not_supported"
    assert workspace.unlock("C-901", 2)["error"] == "wrong_owner"
    model = ScriptedModel([
        [function_call("transfer_to_account_specialist", {}, call_id="handoff-1")],
        [function_call("unlock_account", {"expected_revision": 2}, call_id="unlock-1")],
        [assistant_message("No write was authorized.")],
    ])
    result = run_team(workspace, "C-902", "Inspect locked account", model, False)
    model.assert_complete()
    assert result["case"]["account_locked"] is True
    assert json.loads(workspace.path.read_text())["cases"][1]["handoffs"] == ["account"]


def test_team_registers_exact_specialist_handoff_routes(tmp_path):
    workspace = seeded(tmp_path)
    model = ScriptedModel([[assistant_message("done")]])
    triage, billing, account = make_team(model)
    assert {item.tool_name for item in triage.handoffs} == {"transfer_to_billing_specialist", "transfer_to_account_specialist"}
    assert [item.tool_name for item in billing.handoffs] == ["transfer_to_account_specialist"]
    assert [item.tool_name for item in account.handoffs] == ["transfer_to_billing_specialist"]
