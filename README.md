# Customer escalation team

An actual OpenAI Agents SDK triage agent hands one synthetic support case to billing and account specialists. The application owns case identity, caller intent, role-specific reads and atomic state changes. Billing can issue one exact authorized credit; account support can perform an explicitly requested unlock. Current verification, permission, ownership and revision are checked at the action boundary.

## Run locally

Requires Python3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked --extra test
uv run --locked escalation-team-state init --db workspace/cases.db --fixture fixtures/starter.json
# Configure OPENAI_API_KEY and an explicitly enabled ESCALATION_MODEL.
uv run --locked escalation-team --db workspace/cases.db --case C-903 \
  --request "Resolve the exact duplicate charge and locked account if still authorized" \
  --credit-cents 3500 --unlock --allow-writes
```

Credit amount and unlock are explicit caller action flags, separate from the request text and write permission. Omit --allow-writes for a preview. Readonly SDK control can move between agents, while committed business ownership and effects remain unchanged. Triage has only a read tool and handoff routes. Each specialist must read again after handoff and after consuming a prior read. A stale/foreign/used/wrong-role proof cannot authorize another action. A stored owner or revision alone is not authority.

The normal CLI uses actual pinned Agents0.23.1/OpenAI3.31.0/httpx2 2.13.1SDKs. Fresh agents and async tool/handoff handlers keep actual SQLite ownership on the application loop. Public reset_tool_choice=False preserves the configured auto choice across turns. There are no injected normal model/client/provider objects, shared global client patches, automatic model retries or fallback. Configure a model explicitly; an installed SDK does not enable a live model.

## Source and private human commands

```sh
uv run --locked escalation-team-state inspect --db workspace/cases.db
uv run --locked escalation-team-state intent --db workspace/cases.db --case C-903 \
  --request "Resolve exact requested actions" --credit-cents 3500 --unlock
uv run --locked escalation-team-state update --db workspace/cases.db --case C-903 \
  --updates-json '{"billing_write":false}'
```

These commands are model-free and return no runtime capabilities. An explicit human update can invalidate an outstanding model read. Initialization is exclusive. Existing sources open with SQLite mode=rw and cannot be recreated by reading an absent path. Exact STRICTschema/header/UUID/physical identity, literal original SQL cells/fixture/history and complete observation/decision/role replay are validated without repair. Mutable sources and explicit fixtures must be bounded regular unshared nonsymlink files. Preserve old source and evidence when preparing a separate new workspace.

The original Workspace/run_team and ScriptedModel tests remain explicit trusted human compatibility seams. Normal CLI no longer calls that path. Historical human authoring cannot import a prior native model or authentication claim. The managed source tracks current caller/read/role authority separately from historical business ownership. A live private credential pins the original full authority, so a rehashed SQL permission change cannot promote an existing caller.

## Native completion, evidence and lifecycle

The owned transport checks the whole original Responses JSON before SDK normalization or tool dispatch: explicit completed outer status, no error/incomplete data, complete assistant/function items, permitted current-role tools, unique response/item/call IDs and strict original argument keys/types. Numeric strings, booleans, extra case arguments, duplicate fields, parallel calls, partial/oversized/malformed bodies or incomplete items refuse before their proposed effects. The SDK response is checked separately against the original output. The runner and transport allow at most16sequential turns/requests,1024output tokens per turn,2MiBrequest/1MiBresponse,30second HTTP phases and a shared10..300000millisecond deadline. No sessions, response chaining, tracing writes or real billing/account provider calls are configured. Ambient endpoint/account/admin settings cannot redirect a configured run; ambient custom SDK headers refuse.

Private0700/0600receipts retain original SQL images, original HTTP bytes/unknown fields/counters, SDK output/usage and actual acknowledged source effects. Unknown, zero and invalid original usage stay distinct; billing remains unknown. Final model text, technical completion and independently observed business completion are separate. A completed assistant message can coexist with a denied credit. An incomplete final response, failed close or lost recording directory can coexist with a real earlier credit; there is no rollback claim.

Caller/parent/deadline/direct task cancellation and actual CLI SIGINT/SIGTERM/SIGHUP durably revoke the scope before cancelling/draining work. Cleanup is shielded and closes actual clients/transports/database/descriptors once. Failed close acknowledgement remains unconfirmed even when the actual resource closed. Source corruption leaves current business unknown and retains known earlier effects; control revocation still works where exact header/schema/token permit. A real writer lock can leave durable scope closure unconfirmed. Active persisted state alone is not proof of live execution. This is a cooperative application boundary, not an OS or distributed exactly-once guarantee.

## Verification and remaining gates

```sh
uv run --locked --extra test pytest -q
```

The118-check checkpoint covers five original SDK compatibility flows, complete human/managed source, actual normal SDK/owned HTTP/CLI/handoffs, original completion and argument faults, current permission and identity changes, readonly, all cancellation/signals, missing/partial startup, failed closes, corruption/locks/recording after effects and literal Unicode/file text. Owned HTTP uses synthetic keys/responses and real SDKs; it does not establish live routing quality, billing or actual external credit/unlock.

Next are12coherent private starter/busy/ambiguous/boundary populations and preserving separate reset, then fresh wheel/sdist/complete/full/production-only installations, exact Linux success and equality with its downloaded source artifact. Live enabled provider/key/budget/semantic+billing review, new Firedrill persisted qualification, real billing/account integration and publication/license remain parked. This private repository has no selected public license.

See the [Firedrill example catalog](https://github.com/firedrill-tools/firedrill-example-catalog), [Firedrill](https://firedrill.run/) and [official Firedrill documentation](https://docs.firedrill.run/). The [official OpenAI SDK guide](https://developers.openai.com/api/docs/guides/agents/sdk) and [handoff documentation](https://developers.openai.com/api/docs/guides/agents/orchestration) describe application-owned deployment/storage and SDK control transfer. Exact versions and observed original behavior are in PLAN.md.
