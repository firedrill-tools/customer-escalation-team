"""Exact SQLite source images and replay of scoped escalation observations/decisions."""

from __future__ import annotations

import base64
import re
from copy import deepcopy
from uuid import UUID

from .domain import ROUTES, case, selected, transition
from .source import PROVENANCE, parse
from .strict import canonical, digest, integer, keys, parse_json, sha256, text

APPLICATION_ID = 0x46444543
SOURCE_VERSION = 1
MAX_DATABASE_BYTES = 64 * 1024 * 1024
SCHEMA = {
    "metadata": "CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL) STRICT",
    "business": "CREATE TABLE business (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL) STRICT",
    "operations": "CREATE TABLE operations (id TEXT PRIMARY KEY, document TEXT NOT NULL) STRICT",
    "observations": "CREATE TABLE observations (id INTEGER PRIMARY KEY, document TEXT NOT NULL) STRICT",
    "decisions": "CREATE TABLE decisions (id INTEGER PRIMARY KEY, document TEXT NOT NULL) STRICT",
    "seed_state": "CREATE TABLE seed_state (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL) STRICT",
}
LIMITS = {
    "metadata": 1,
    "business": 1,
    "operations": 128,
    "observations": 1024,
    "decisions": 512,
    "seed_state": 1,
}


def uuid(value):
    if type(value) is not str or str(UUID(value)) != value or UUID(value).version != 4:
        raise ValueError("original UUID4 escalation identity required")
    return value


def hex_digest(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("original SHA256 required")
    return value


def schema(db):
    if (
        db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        or db.execute("PRAGMA user_version").fetchone()[0] != SOURCE_VERSION
        or db.execute("PRAGMA page_size").fetchone()[0] != 4096
        or db.execute("PRAGMA encoding").fetchone()[0] != "UTF-8"
        or db.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
    ):
        raise ValueError("original escalation database header differs")
    if db.execute("PRAGMA page_count").fetchone()[0] * 4096 > MAX_DATABASE_BYTES:
        raise ValueError("escalation database exceeds64MiB")
    actual = [
        tuple(row)
        for row in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        )
    ]
    expected = sorted(
        [("table", name, name, sql) for name, sql in SCHEMA.items()]
        + [("index", "sqlite_autoindex_operations_1", "operations", None)]
    )
    if actual != expected or [
        tuple(r) for r in db.execute("PRAGMA quick_check(1)")
    ] != [("ok",)]:
        raise ValueError("exact original escalation SQL schema/integrity required")
    return actual


def rows(db, name):
    count = db.execute("SELECT count(*) FROM " + name).fetchone()[0]
    if count > LIMITS[name]:
        raise ValueError("bounded complete escalation SQL rows required")
    original = []
    values = []
    for identity, raw in db.execute(
        "SELECT id,CAST(document AS BLOB) FROM " + name + " ORDER BY id"
    ):
        if type(raw) is not bytes:
            raise ValueError("original escalation SQL text bytes required")
        value = parse_json(raw)
        if raw.decode("utf-8", errors="strict").encode("utf-8", errors="strict") != raw:
            raise ValueError("original escalation SQL UTF8 differs")
        original.append(
            {
                "id": identity,
                "document_base64": base64.b64encode(raw).decode(),
                "document_sha256": sha256(raw),
            }
        )
        values.append((identity, value))
    return original, values


def header(value, physical, instance_id=None):
    keys(
        value,
        {
            "schemaVersion",
            "kind",
            "instance_id",
            "physical_identity",
            "fixture_base64",
            "fixture_sha256",
            "provenance",
            "creation",
            "population",
        },
    )
    if (
        type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != SOURCE_VERSION
        or value["kind"] != "managed_synthetic_escalation_source"
        or value["physical_identity"] != physical
        or value["provenance"] != PROVENANCE
        or value["creation"] not in ("fixture", "population")
        or value["creation"] == "fixture"
        and value["population"] is not None
    ):
        raise ValueError(
            "original escalation source header identity/provenance differs"
        )
    uuid(value["instance_id"])
    if instance_id is not None and value["instance_id"] != instance_id:
        raise ValueError("original escalation source UUID differs")
    if type(value["fixture_base64"]) is not str:
        raise ValueError("literal escalation initial fixture required")
    raw = base64.b64decode(value["fixture_base64"], validate=True)
    if (
        base64.b64encode(raw).decode() != value["fixture_base64"]
        or sha256(raw) != value["fixture_sha256"]
    ):
        raise ValueError("original escalation fixture hash/encoding differs")
    if value["creation"] == "population":
        from .populations import baseline

        return baseline(value["population"], raw)
    return parse(raw)["business"]


def context(source, frontier, business):
    value = {
        "source_sha256": digest(source),
        "frontier": frontier,
        "business_sha256": digest(business),
    }
    return value, digest(value)


def caller_intent(
    business, case_id, request, context_sha256, credit_cents=None, unlock=False
):
    text(request, 1, 8192)
    if not request.strip() or type(unlock) is not bool:
        raise ValueError("nonblank caller request and exact unlock intent required")
    if credit_cents is not None:
        integer(credit_cents, 1)
        if (
            credit_cents > 5000
            or credit_cents != selected(business, case_id)["charge_cents"]
        ):
            raise ValueError(
                "explicit caller credit must match exact current supported charge"
            )
    return {
        "case_id": case_id,
        "case": deepcopy(selected(business, case_id)),
        "request": request,
        "credit_cents": credit_cents,
        "unlock": unlock,
        "context_sha256": hex_digest(context_sha256),
    }


def check_operation(value):
    keys(
        value,
        {
            "id",
            "token_sha256",
            "task_sha256",
            "intent",
            "initial_frontier",
            "allow_writes",
            "status",
            "closed_frontier",
            "closed_observations",
        },
    )
    uuid(value["id"])
    hex_digest(value["token_sha256"])
    hex_digest(value["task_sha256"])
    integer(value["initial_frontier"])
    if type(value["allow_writes"]) is not bool or value["status"] not in (
        "active",
        "closed",
    ):
        raise ValueError("exact escalation operation permission/status required")
    keys(
        value["intent"],
        {"case_id", "case", "request", "credit_cents", "unlock", "context_sha256"},
    )
    if value["status"] == "active":
        if (
            value["closed_frontier"] is not None
            or value["closed_observations"] is not None
        ):
            raise ValueError("active escalation operation has closure frontier")
    else:
        integer(value["closed_frontier"])
        integer(value["closed_observations"])
        if value["closed_frontier"] < value["initial_frontier"]:
            raise ValueError("escalation closure precedes caller intent")


def public_read(business, operation):
    return {"case": deepcopy(selected(business, operation["intent"]["case_id"]))}


def evaluate(business, operation, role, action, observation):
    keys(action, {"kind", "args"})
    kind, args = action["kind"], action["args"]
    extra = (
        {"actor", "to"}
        if kind == "handoff"
        else (
            {"expected_revision", "amount_cents"}
            if kind == "credit"
            else {"expected_revision"} if kind == "unlock" else None
        )
    )
    if extra is None:
        raise ValueError("bounded SDK handoff or specialist action required")
    keys(args, {"case_id", "source"} | extra)
    original = case(args["source"])
    if (
        args["case_id"] != operation["intent"]["case_id"]
        or original != observation["value"]["case"]
        or observation["role"] != role
    ):
        raise ValueError("exact own selected case/role read required")
    if kind == "handoff":
        if args["actor"] != role or args["to"] not in ROUTES[role]:
            raise ValueError("actual SDK handoff route differs from current role")
    else:
        if role != ("billing" if kind == "credit" else "account"):
            raise ValueError("specialist cannot borrow another role's action")
        integer(args["expected_revision"], 1)
        if kind == "credit":
            integer(args["amount_cents"], 1)
    if (
        kind == "credit" and operation["intent"]["credit_cents"] != args["amount_cents"]
    ) or (kind == "unlock" and not operation["intent"]["unlock"]):
        return business, {"status": "caller_intent_required"}, "denied", role
    if not operation["allow_writes"]:
        # SDK control may route a readonly preview; business ownership stays literal.
        return (
            business,
            {"status": "write_permission_required"},
            "denied",
            args["to"] if kind == "handoff" else role,
        )
    after, result = transition(business, action)
    role_after = (
        args["to"]
        if kind == "handoff" and result["status"] == "handoff_applied"
        else role
    )
    return after, result, "applied" if after != business else "unchanged", role_after


def snapshot(db, physical, instance_id=None):
    original_schema = schema(db)
    raw, table = {}, {}
    for name in SCHEMA:
        raw[name], table[name] = rows(db, name)
    if [r[0] for r in table["metadata"]] != [1] or [
        r[0] for r in table["business"]
    ] != [1]:
        raise ValueError("original escalation singleton/fixture seed differs")
    source = table["metadata"][0][1]
    current = header(source, physical, instance_id)
    if source["creation"] == "population":
        from .populations import validate_seed

        if [r[0] for r in table["seed_state"]] != [1]:
            raise ValueError("original case population seed required")
        validate_seed(source, original_schema, raw, table["seed_state"][0][1])
    elif table["seed_state"]:
        raise ValueError("fixture cannot import population control")
    operations = {}
    for identity, value in table["operations"]:
        check_operation(value)
        if identity != value["id"]:
            raise ValueError("original escalation operation SQL identity differs")
        operations[identity] = value
    if sum(o["status"] == "active" for o in operations.values()) > 1:
        raise ValueError("one active escalation operation required")
    observations = {}
    for expected, (identity, value) in enumerate(table["observations"], 1):
        keys(
            value,
            {
                "id",
                "operation_id",
                "frontier",
                "context_sha256",
                "role",
                "value",
                "proof_sha256",
            },
        )
        if (
            type(identity) is not int
            or identity != expected
            or type(value["id"]) is not int
            or value["id"] != identity
            or value["operation_id"] not in operations
            or type(value["role"]) is not str
            or value["role"] not in ROUTES
        ):
            raise ValueError("exact escalation observation SQL/owner/role required")
        integer(value["frontier"])
        hex_digest(value["context_sha256"])
        hex_digest(value["proof_sha256"])
        observations[identity] = value
    frontiers = [deepcopy(current)]
    role_frontiers = [{ident: "triage" for ident in operations}]
    used, decisions = set(), []
    for ordinal, (identity, value) in enumerate(table["decisions"], 1):
        keys(
            value,
            {
                "id",
                "action",
                "operation_id",
                "read_id",
                "before_context_sha256",
                "after_context_sha256",
                "status",
                "result",
                "provenance",
            },
        )
        if (
            type(identity) is not int
            or identity != ordinal
            or type(value["id"]) is not int
            or value["id"] != ordinal
        ):
            raise ValueError("original escalation decision frontier differs")
        _, before = context(source, ordinal - 1, current)
        roles = deepcopy(role_frontiers[-1])
        if value["before_context_sha256"] != before:
            raise ValueError("original escalation before context differs")
        if value["operation_id"] is None:
            if (
                value["read_id"] is not None
                or value["provenance"] != "explicit_synthetic_human_update"
                or value["action"].get("kind") != "update"
            ):
                raise ValueError(
                    "human managed boundary only permits explicit case updates"
                )
            after, result = transition(current, value["action"])
            status = "applied" if after != current else "unchanged"
        else:
            if (
                type(value["operation_id"]) is not str
                or type(value["read_id"]) is not int
            ):
                raise ValueError("exact escalation decision owner/read required")
            operation = operations.get(value["operation_id"])
            read = observations.get(value["read_id"])
            if (
                operation is None
                or read is None
                or value["read_id"] in used
                or read["operation_id"] != operation["id"]
                or read["frontier"] != ordinal - 1
                or read["context_sha256"] != before
                or value["provenance"] != "managed_caller"
                or operation["initial_frontier"] > ordinal - 1
                or operation["status"] == "closed"
                and ordinal > operation["closed_frontier"]
            ):
                raise ValueError(
                    "genuine own current unused escalation observation required"
                )
            used.add(value["read_id"])
            after, result, status, roles[operation["id"]] = evaluate(
                current, operation, roles[operation["id"]], value["action"], read
            )
        _, after_context = context(source, ordinal, after)
        if (
            value["result"] != result
            or value["status"] != status
            or value["after_context_sha256"] != after_context
        ):
            raise ValueError("original escalation decision replay/result differs")
        current = after
        frontiers.append(deepcopy(current))
        role_frontiers.append(roles)
        decisions.append(value)
    for operation in operations.values():
        frontier = operation["initial_frontier"]
        if (
            frontier >= len(frontiers)
            or operation["status"] == "closed"
            and (
                operation["closed_frontier"] >= len(frontiers)
                or operation["closed_observations"] > len(observations)
            )
        ):
            raise ValueError("original escalation caller/closure frontier absent")
        _, ctx = context(source, frontier, frontiers[frontier])
        intent = operation["intent"]
        if intent != caller_intent(
            frontiers[frontier],
            intent["case_id"],
            intent["request"],
            ctx,
            intent["credit_cents"],
            intent["unlock"],
        ):
            raise ValueError("complete original selected case/caller intent differs")
    for read in observations.values():
        frontier = read["frontier"]
        operation = operations[read["operation_id"]]
        if (
            frontier >= len(frontiers)
            or frontier < operation["initial_frontier"]
            or operation["status"] == "closed"
            and (
                frontier > operation["closed_frontier"]
                or read["id"] > operation["closed_observations"]
            )
        ):
            raise ValueError("original escalation observation frontier absent")
        _, ctx = context(source, frontier, frontiers[frontier])
        if (
            read["context_sha256"] != ctx
            or read["role"] != role_frontiers[frontier][operation["id"]]
            or read["value"] != public_read(frontiers[frontier], operation)
        ):
            raise ValueError(
                "original escalation observation case/role/context differs"
            )
    if table["business"][0][1] != current:
        raise ValueError("complete current escalation business differs from replay")
    ctx, ctx_sha = context(source, len(decisions), current)
    original = {"schema": original_schema, "tables": raw}
    return {
        "source": deepcopy(source),
        "business": deepcopy(current),
        "records": {
            "operations": list(operations.values()),
            "observations": list(observations.values()),
            "decisions": decisions,
        },
        "roles": role_frontiers[-1],
        "context": ctx,
        "context_sha256": ctx_sha,
        "original": original,
        "sha256": digest(original),
    }
