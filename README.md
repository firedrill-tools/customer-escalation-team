# Customer escalation team

An OpenAI Agents SDK triage agent hands a synthetic case to billing and account specialists. Handoffs retain one shared case ID and record the current owner. Billing can issue an exact credit for a verified duplicate charge; account support can unlock a verified account. Every specialist rereads current permissions and revision before a write, so a permission change after handoff blocks the action.

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --extra test
cp fixtures/starter.json workspace.json
```

Set `OPENAI_API_KEY` and an enabled `ESCALATION_MODEL`, then inspect a case without writes:

```sh
uv run escalation-team --case C-901 --request "Review this customer's duplicate charge and route it to the right specialist"
```

To allow a requested synthetic action:

```sh
uv run escalation-team --case C-903 --request "Resolve the exact duplicate charge and locked account if both permissions still allow it" --allow-writes
```

The shared `CaseContext` carries the fixed case ID through SDK handoffs. Triage has only a read tool. Each specialist has its own bounded write tool, and an `on_handoff` callback records ownership in the local workspace. Specialist tools check owner, verification, current permission, exact amount or lock state, and revision. The write flag is required even when a tool is present. The final JSON reports the last agent, SDK usage when available, committed case and audit trail.

The fixture has billing-only, account-only and mixed cases. All addresses and amounts are synthetic; no real billing or identity provider is called. Reset by copying the fixture over `workspace.json`. The JSON store is for sequential local runs rather than concurrent production cases.

`uv run --extra test pytest` uses the Agents SDK's scripted model to execute real triage-to-specialist and specialist-to-specialist handoffs, tool calls, shared context and permission revocation. This verifies orchestration and local policy, not live model routing quality. Live OpenAI and new Firedrill integration proofs remain pending. This private repository has no license pending portfolio publication decisions.
