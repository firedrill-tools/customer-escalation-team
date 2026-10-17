"""Owned normal Agents SDK workflow and lifecycle; all effects are local synthetic state."""

import asyncio
import time
from copy import deepcopy
from datetime import datetime, timezone

from .domain import selected
from .evidence import Receipt, error_info
from .managed import ManagedSource
from .native import Native, config
from .strict import canonical, parse_json

TASK = "Resolve this fixed synthetic support case through actual triage/specialist handoffs and only the exact caller-authorized credit or unlock."


class Run:
    def __init__(self, receipt, timeout_ms):
        self.receipt = receipt
        self.deadline = time.monotonic() + timeout_ms / 1000
        self.source = self.scope = self.native = self.job = self.intent = None
        self.failure = None
        self.closing = self.scope_close_attempted = self.scope_closed = False
        self.flow_drained = True
        self.source_attempted = False
        self.stop_events = []
        self.active_role = "triage"
        self.reads, self.agents = {}, {}
        self.allow_writes = False

    def fail(self, error):
        if self.failure is None:
            self.failure = error

    def save(self):
        try:
            self.receipt.save()
        except BaseException as error:
            self.fail(error)
            raise

    def basic(self):
        if self.failure is not None:
            raise self.failure
        if self.closing:
            raise RuntimeError("escalation operation is closing")
        if any(event.is_set() for event in self.stop_events):
            raise RuntimeError("escalation cancellation requested")
        if time.monotonic() >= self.deadline:
            raise TimeoutError("escalation shared deadline exceeded")
        if self.receipt.error is not None:
            raise self.receipt.error

    def check(self):
        self.basic()
        if self.source is not None and self.scope is not None:
            self.source._operation(self.source.snapshot(), self.scope)

    def close_scope(self):
        if self.scope_close_attempted:
            return
        if self.scope is None:
            self.scope_closed = True
            return
        self.scope_close_attempted = True
        try:
            result = self.source.close_operation(self.scope)
            self.scope_closed = result["closed"]
            self.receipt.data["scope_close"] = result
            self.receipt.data["lifecycle"].append(
                {"kind": "scope_close_returned", "closed": self.scope_closed}
            )
        except BaseException as error:
            self.receipt.data["scope_close_error"] = error_info(error)
            self.fail(error)

    def cancel(self, error):
        if self.closing:
            self.fail(error)
            return
        self.closing = True
        self.fail(error)
        self.close_scope()
        if self.job is not None and not self.job.done():
            self.receipt.data["lifecycle"].append(
                {"kind": "flow_cancel_requested", "scope_closed": self.scope_closed}
            )
            self.job.cancel()

    def context(self, wrapper):
        self.check()
        context = wrapper.context
        if (
            context.root is not self
            or context.case_id != self.intent["case_id"]
            or type(context.allow_writes) is not bool
            or context.allow_writes != self.allow_writes
        ):
            raise ValueError("original private shared case context required")

    def agent_role(self, agent):
        for role, expected in self.agents.items():
            if agent is expected:
                return role
        raise ValueError("actual registered per-run SDK agent required")

    def consume(self, wrapper, name, arguments):
        call_id = getattr(wrapper, "tool_call_id", None)
        raw = getattr(wrapper, "tool_arguments", None)
        if (
            type(call_id) is not str
            or type(raw) is not str
            or parse_json(raw.encode("utf-8", errors="strict")) != arguments
            or getattr(wrapper, "tool_name", None) != name
        ):
            raise ValueError("actual exact SDK ToolContext required")
        return self.native.consume(name, arguments, call_id)

    def read(self, wrapper):
        self.consume(wrapper, "get_case", {})
        read = self.source.observe(self.scope, self.active_role)
        self.reads[self.active_role] = read
        self.receipt.data["actions"].append(
            {
                "kind": "own_current_role_read",
                "role": self.active_role,
                "observation": deepcopy(read["observation"]),
                "source": self.receipt.source(self.source.snapshot()),
            }
        )
        self.save()
        return deepcopy(read["value"])

    def apply(self, role, kind, args):
        self.check()
        if self.active_role != role or role not in self.reads:
            raise ValueError(
                "own fresh SDK role read required before handoff or action"
            )
        read = self.reads[role]
        row = read["value"]["case"]
        action = {
            "kind": kind,
            "args": {
                "case_id": self.intent["case_id"],
                "source": deepcopy(row),
                **deepcopy(args),
            },
        }
        result = self.source.commit(
            self.scope, role, action, read["observation"]["id"], read["proof"]
        )
        self.reads.pop(role)
        self.receipt.data["acknowledged_decisions"].append(deepcopy(result))
        if result["decision"]["status"] == "applied":
            self.receipt.data["acknowledged_effects"].append(deepcopy(result))
        snapshot = self.source.snapshot()
        self.receipt.data["observed_business"] = observed(snapshot, self.intent)
        self.receipt.data["actions"].append(
            {
                "kind": "source_decision",
                "role": role,
                "result": deepcopy(result),
                "source": self.receipt.source(snapshot),
            }
        )
        self.save()
        return result["result"]

    def action(self, wrapper, role, kind, args):
        self.consume(
            wrapper, "issue_credit" if kind == "credit" else "unlock_account", args
        )
        return self.apply(role, kind, args)

    def handoff(self, target):
        self.native.consume("transfer_to_" + target + "_specialist", {})
        result = self.apply(
            self.active_role, "handoff", {"actor": self.active_role, "to": target}
        )
        if result["status"] not in ("handoff_applied", "write_permission_required"):
            raise ValueError("SDK ownership handoff was not accepted")


def observed(source, intent):
    row = selected(source["business"], intent["case_id"])
    prior = intent["case"]
    credits = [
        r
        for r in source["business"]["audit"]
        if r["case_id"] == row["id"]
        and r["event"] == "credit_issued"
        and r["amount_cents"] == intent["credit_cents"]
        and r["source"]["customer"] == prior["customer"]
        and r["source"]["charge_cents"] == prior["charge_cents"]
    ]
    credit_complete = (
        bool(row["credit_issued"] and credits)
        if intent["credit_cents"] is not None
        else None
    )
    unlock_complete = not row["account_locked"] if intent["unlock"] else None
    requested = credit_complete is not None or unlock_complete is not None
    return {
        "status": "valid",
        "as_observed_context_sha256": source["context_sha256"],
        "case": deepcopy(row),
        "credit_complete": credit_complete,
        "unlock_complete": unlock_complete,
        "workflow_complete": (
            all(x for x in (credit_complete, unlock_complete) if x is not None)
            if requested
            else None
        ),
        "actual_external_effect": None,
    }


def reported_usage(calls):
    output = {
        "source": "original Responses counters across actual HTTP calls",
        "cost_usd": None,
    }
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        counters = [
            call.get("reported_usage", {}).get(
                field, {"status": "unknown", "value": None}
            )
            for call in calls
        ]
        subtotal = sum(c["value"] for c in counters if c["status"] == "observed")
        known = bool(counters) and all(c["status"] == "observed" for c in counters)
        output[field] = {
            "status": "observed" if known else "unknown",
            "value": subtotal if known else None,
            "observed_subtotal": subtotal,
        }
    return output


async def run_owned_team(**options):
    allowed = {
        "database",
        "case_id",
        "request",
        "credit_cents",
        "unlock",
        "allow_writes",
        "intent",
        "api_key",
        "model",
        "base_url",
        "output_root",
        "timeout_ms",
        "stop_event",
        "parent_event",
    }
    signals = [options.get("stop_event"), options.get("parent_event")]
    receipt, owner, timer, watchers, result = None, None, None, [], None
    try:
        receipt = Receipt(
            options.get("output_root") or "artifacts/customer-escalation",
            {
                "execution": "actual_owned_Agents_OpenAI_Responses_SDK",
                "allocation": {"source": False, "scope": False, "provider": False},
                "observed_business": {
                    "status": "unopened",
                    "actual_external_effect": None,
                },
            },
        )
        if set(options) - allowed:
            raise ValueError("unsupported model/client/provider or execution option")
        if options.get("model") is not None and type(options["model"]) is not str:
            raise ValueError(
                "explicit model identifier required; model objects are unsupported"
            )
        frozen = {
            key: deepcopy(value)
            for key, value in options.items()
            if key not in ("stop_event", "parent_event")
        }
        timeout_ms = frozen.get("timeout_ms", 120000)
        if type(timeout_ms) is not int or not 10 <= timeout_ms <= 300000:
            raise ValueError("exact bounded timeout_ms10..300000 required")
        owner = Run(receipt, timeout_ms)
        if type(frozen.get("allow_writes", False)) is not bool:
            raise ValueError("exact independent allow_writes required")
        owner.allow_writes = frozen.get("allow_writes", False)
        for event in signals:
            if event is not None:
                if not isinstance(event, asyncio.Event):
                    raise ValueError("actual asyncio cancellation Event required")
                owner.stop_events.append(event)
        owner.basic()
        configuration = config(
            frozen.get("api_key"), frozen.get("model"), frozen.get("base_url")
        )
        owner.basic()
        loop = asyncio.get_running_loop()
        timer = loop.call_later(
            timeout_ms / 1000,
            lambda: owner.cancel(TimeoutError("escalation shared deadline exceeded")),
        )
        for event in owner.stop_events:

            async def watch(signal=event):
                await signal.wait()
                owner.cancel(RuntimeError("escalation cancellation requested"))

            watchers.append(asyncio.create_task(watch()))
        owner.source_attempted = True
        try:
            owner.source = ManagedSource(
                frozen.get("database"), commit_guard=owner.basic
            )
        except BaseException as error:
            owner.source = getattr(error, "managed_source", None)
            receipt.data["allocation"]["source"] = owner.source is not None and (
                owner.source.database is not None or owner.source.descriptor is not None
            )
            raise
        receipt.data["allocation"]["source"] = True
        initial = owner.source.snapshot()
        receipt.data["initial_source"] = receipt.source(initial)
        owner.intent = owner.source.intent(
            frozen.get("case_id"),
            frozen.get("request"),
            credit_cents=frozen.get("credit_cents"),
            unlock=frozen.get("unlock", False),
        )
        if frozen.get("intent") is not None and frozen["intent"] != owner.intent:
            raise ValueError("complete current caller case intent required")
        receipt.data["caller_intent"] = deepcopy(owner.intent)
        receipt.data["observed_business"] = observed(initial, owner.intent)
        owner.scope = owner.source.begin(
            owner.intent, TASK, allow_writes=owner.allow_writes
        )
        receipt.data["allocation"]["scope"] = True
        try:
            owner.native = Native(owner, configuration)
        except BaseException as error:
            owner.native = getattr(error, "native_owner", None)
            receipt.data["allocation"]["provider"] = owner.native is not None and (
                owner.native.transport is not None
                or owner.native.http is not None
                or owner.native.client is not None
            )
            raise
        receipt.data["allocation"]["provider"] = True
        from agents import RunConfig, Runner
        from .owned_team import CaseContext, Hooks, make_team

        triage = make_team(owner)
        public = {
            "case_id": owner.intent["case_id"],
            "request": owner.intent["request"],
            "requested_credit_cents": owner.intent["credit_cents"],
            "requested_unlock": owner.intent["unlock"],
        }
        owner.job = asyncio.create_task(
            Runner.run(
                triage,
                canonical(public),
                context=CaseContext(owner, owner.intent["case_id"], owner.allow_writes),
                max_turns=16,
                hooks=Hooks(owner),
                run_config=RunConfig(
                    tracing_disabled=True, trace_include_sensitive_data=False
                ),
                auto_previous_response_id=False,
            )
        )
        owner.flow_drained = False
        result = await asyncio.shield(owner.job)
        owner.flow_drained = True
        owner.check()
        final = receipt.data["calls"][-1]
        if (
            type(result.final_output) is not str
            or result.final_output != final["completion"]["final_text"]
            or not final.get("sdk_verified")
        ):
            raise ValueError(
                "actual SDK final differs from complete original assistant output"
            )
        receipt.data["sdk_final"] = {
            "output": result.final_output,
            "role": owner.agent_role(result.last_agent),
            "last_agent": result.last_agent.name,
            "usage": {
                field: getattr(result.context_wrapper.usage, field, None)
                for field in (
                    "requests",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                )
            },
        }
        owner.save()
    except BaseException as error:
        if receipt is None:
            receipt = getattr(error, "receipt", None)
        if owner is None and receipt is not None:
            owner = Run(receipt, 120000)
        if owner is None:
            raise
        owner.fail(error)
    finally:
        if owner is not None:
            owner.closing = True
            owner.close_scope()

            async def finish():
                owner.close_scope()
                if owner.job is not None:
                    if not owner.job.done():
                        receipt.data["lifecycle"].append(
                            {
                                "kind": "flow_cancel_requested",
                                "scope_closed": owner.scope_closed,
                            }
                        )
                        owner.job.cancel()
                    await asyncio.gather(owner.job, return_exceptions=True)
                    owner.flow_drained = True
                provider_confirmed = owner.native is None
                if owner.native is not None:
                    try:
                        await owner.native.close()
                        provider_confirmed = owner.native.closed
                    except BaseException as error:
                        owner.fail(error)
                        receipt.data["provider_close_error"] = error_info(error)
                source_confirmed, cleanup = owner.source is None, None
                if owner.source is not None:
                    try:
                        snapshot = owner.source.snapshot()
                        receipt.data["final_source"] = receipt.source(snapshot)
                        if owner.intent is not None:
                            receipt.data["observed_business"] = observed(
                                snapshot, owner.intent
                            )
                    except BaseException as error:
                        owner.fail(error)
                        receipt.data["observed_business"] = {
                            "status": "unknown",
                            "last_known": receipt.data["observed_business"],
                            "error": error_info(error),
                            "actual_external_effect": None,
                        }
                    try:
                        cleanup = owner.source.close()
                        source_confirmed = (
                            cleanup["database_closed"]
                            or not cleanup["database_close_attempted"]
                        ) and (
                            cleanup["descriptor_closed"]
                            or not cleanup["descriptor_close_attempted"]
                        )
                    except BaseException as error:
                        owner.fail(error)
                        receipt.data["source_close_error"] = error_info(error)
                        cleanup = {
                            "database_close_attempted": owner.source.database_close_attempted,
                            "database_closed": owner.source.database_closed,
                            "descriptor_close_attempted": owner.source.descriptor_close_attempted,
                            "descriptor_closed": owner.source.descriptor_closed,
                            "close_errors": deepcopy(owner.source.close_errors),
                        }
                for task in watchers:
                    task.cancel()
                if watchers:
                    await asyncio.gather(*watchers, return_exceptions=True)
                if timer is not None:
                    timer.cancel()
                if time.monotonic() >= owner.deadline:
                    owner.fail(
                        TimeoutError("escalation deadline exceeded during cleanup")
                    )
                receipt.data["cleanup"] = {
                    "scope_close_attempted": owner.scope_close_attempted,
                    "scope_closed": owner.scope_closed,
                    "flow_drained": owner.flow_drained,
                    "provider_close_attempted": (
                        owner.native.close_attempted if owner.native else False
                    ),
                    "provider_closed": owner.native.closed if owner.native else True,
                    "provider_close_confirmed": provider_confirmed,
                    "source_close_confirmed": source_confirmed,
                    "source": cleanup,
                }
                receipt.data["cleanup_confirmed"] = (
                    owner.scope_closed
                    and owner.flow_drained
                    and provider_confirmed
                    and source_confirmed
                )
                if not receipt.data["cleanup_confirmed"]:
                    owner.fail(RuntimeError("escalation cleanup unconfirmed"))
                receipt.data["source_attempted"] = owner.source_attempted
                receipt.data["finished_at"] = datetime.now(timezone.utc).isoformat()
                receipt.data["status"] = "failed" if owner.failure else "completed"
                receipt.data["error"] = (
                    error_info(owner.failure) if owner.failure else None
                )
                receipt.data["result"] = (
                    {
                        "status": "completed" if owner.allow_writes else "read_only",
                        "response": receipt.data["sdk_final"]["output"],
                        "last_agent": receipt.data["sdk_final"]["last_agent"],
                        "business": receipt.data["observed_business"],
                        "usage": reported_usage(receipt.data["calls"]),
                        "cost_usd": None,
                        "receipt_path": str(receipt.path),
                        "cleanup_confirmed": receipt.data["cleanup_confirmed"],
                        "actual_external_effect": None,
                    }
                    if owner.failure is None
                    else None
                )
                try:
                    owner.save()
                    if time.monotonic() >= owner.deadline:
                        raise TimeoutError(
                            "escalation deadline exceeded during final recording"
                        )
                except BaseException as error:
                    owner.fail(error)
                    receipt.data["status"] = "failed"
                    receipt.data["result"] = None
                    receipt.data["error"] = error_info(owner.failure)
                    if receipt.error is None:
                        try:
                            receipt.save()
                        except BaseException:
                            pass

            cleanup_task = asyncio.create_task(finish())
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError as error:
                    owner.fail(error)
            cleanup_task.result()
    if owner.failure is not None:
        error = (
            RuntimeError("escalation caller cancelled")
            if isinstance(owner.failure, asyncio.CancelledError)
            else owner.failure
        )
        error.evidence = receipt.data
        error.receipt_path = str(receipt.path) if receipt.path else None
        if error is not owner.failure:
            raise error from owner.failure
        raise error
    return receipt.data["result"]
