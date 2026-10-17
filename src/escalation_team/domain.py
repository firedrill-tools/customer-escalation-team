"""Pure current case/ownership/credit/unlock policy; source owns caller authority."""

from copy import deepcopy

from .strict import identifier, integer, keys, next_revision, text

ROLES = ("triage", "billing", "account")
FIELDS = {
    "id",
    "customer",
    "request",
    "verified",
    "charge_cents",
    "credit_issued",
    "account_locked",
    "billing_write",
    "account_write",
    "owner",
    "revision",
    "handoffs",
}
BOOLS = (
    "verified",
    "credit_issued",
    "account_locked",
    "billing_write",
    "account_write",
)
UPDATE_FIELDS = {
    "customer",
    "request",
    "verified",
    "charge_cents",
    "account_locked",
    "billing_write",
    "account_write",
}
ROUTES = {
    "triage": ("billing", "account"),
    "billing": ("account",),
    "account": ("billing",),
}


def case(value):
    keys(value, FIELDS)
    identifier(value["id"])
    text(value["customer"], 1, 200)
    text(value["request"], 1, 2000)
    for field in BOOLS:
        if type(value[field]) is not bool:
            raise ValueError("exact current case boolean required")
    integer(value["charge_cents"])
    integer(value["revision"], 1)
    if (
        type(value["owner"]) is not str
        or value["owner"] not in ROLES
        or type(value["handoffs"]) is not list
        or len(value["handoffs"]) > 128
        or any(
            type(role) is not str or role not in ("billing", "account")
            for role in value["handoffs"]
        )
    ):
        raise ValueError("bounded exact case ownership history required")
    if value["owner"] != (value["handoffs"][-1] if value["handoffs"] else "triage"):
        raise ValueError("case owner differs from its handoff history")
    return value


def validate(value):
    keys(value, {"cases", "audit"})
    if (
        type(value["cases"]) is not list
        or not 1 <= len(value["cases"]) <= 128
        or type(value["audit"]) is not list
        or len(value["audit"]) > 2048
    ):
        raise ValueError("complete bounded escalation tables required")
    seen = [case(row)["id"] for row in value["cases"]]
    if len(seen) != len(set(seen)):
        raise ValueError("unique complete escalation case IDs required")
    for entry in value["audit"]:
        if type(entry) is not dict or entry.get("event") not in (
            "handoff",
            "credit_issued",
            "account_unlocked",
            "case_updated",
        ):
            raise ValueError("known original case audit required")
        extra = (
            {"actor", "to"}
            if entry["event"] == "handoff"
            else (
                {"amount_cents"}
                if entry["event"] == "credit_issued"
                else {"updates"} if entry["event"] == "case_updated" else set()
            )
        )
        keys(entry, {"case_id", "event", "source", "revision"} | extra)
        original = case(entry["source"])
        if (
            entry["case_id"] not in seen
            or entry["case_id"] != original["id"]
            or type(entry["revision"]) is not int
            or entry["revision"] != next_revision(original["revision"])
        ):
            raise ValueError("original audit case/revision linkage differs")
    return value


def initial(value):
    explicit = type(value) is dict and "baselineVersion" in value
    keys(value, {"cases", "audit"} | ({"baselineVersion"} if explicit else set()))
    if explicit and (
        type(value["baselineVersion"]) is not int or value["baselineVersion"] != 1
    ):
        raise ValueError("explicit case baselineVersion1 required")
    current = {k: deepcopy(value[k]) for k in ("cases", "audit")}
    validate(current)
    if current["audit"] or any(
        row["owner"] != "triage"
        or row["handoffs"]
        or row["credit_issued"]
        or not explicit
        and row["revision"] != 1
        for row in current["cases"]
    ):
        raise ValueError(
            "original case baseline cannot import prior ownership/credit/audit"
        )
    return current


def selected(value, case_id):
    identifier(case_id)
    row = next((r for r in value["cases"] if r["id"] == case_id), None)
    if row is None:
        raise ValueError("unknown escalation case")
    return row


def transition(value, action):
    validate(value)
    keys(action, {"kind", "args"})
    kind, args = action["kind"], action["args"]
    extra = (
        {"actor", "to"}
        if kind == "handoff"
        else (
            {"expected_revision", "amount_cents"}
            if kind == "credit"
            else (
                {"expected_revision"}
                if kind == "unlock"
                else {"updates"} if kind == "update" else None
            )
        )
    )
    if extra is None:
        raise ValueError("known bounded escalation action required")
    keys(args, {"case_id", "source"} | extra)
    observed = case(args["source"])
    if observed["id"] != args["case_id"]:
        raise ValueError("exact selected case source required")
    current = deepcopy(value)
    row = selected(current, args["case_id"])
    if kind == "handoff":
        if (
            type(args["actor"]) is not str
            or args["actor"] not in ROUTES
            or args["to"] not in ROUTES[args["actor"]]
        ):
            raise ValueError("known actual specialist handoff route required")
    elif kind in ("credit", "unlock"):
        integer(args["expected_revision"], 1)
        if kind == "credit":
            integer(args["amount_cents"], 1)
    else:
        updates = args["updates"]
        if (
            type(updates) is not dict
            or not updates
            or not set(updates) <= UPDATE_FIELDS
        ):
            raise ValueError("exact nonempty human case updates required")
        case({**deepcopy(row), **deepcopy(updates)})
    if row != observed:
        return current, {"status": "case_changed"}
    if kind == "handoff":
        if args["actor"] != "triage" and row["owner"] != args["actor"]:
            return current, {"status": "wrong_owner"}
        revision = next_revision(row["revision"])
        if len(row["handoffs"]) == 128:
            raise ValueError("case handoff history exhausted")
        row["owner"] = args["to"]
        row["handoffs"].append(args["to"])
        audit = {"event": "handoff", "actor": args["actor"], "to": args["to"]}
        result = {
            "status": "handoff_applied",
            "case_id": row["id"],
            "owner": row["owner"],
            "revision": revision,
        }
    elif kind in ("credit", "unlock"):
        owner = "billing" if kind == "credit" else "account"
        if row["owner"] != owner:
            return current, {"status": "wrong_owner"}
        if not row["verified"] or not row[owner + "_write"]:
            return current, {"status": "permission_denied"}
        if row["revision"] != args["expected_revision"]:
            return current, {"status": "stale_revision"}
        if kind == "credit":
            if row["credit_issued"]:
                return current, {"status": "already_credited"}
            if (
                args["amount_cents"] != row["charge_cents"]
                or args["amount_cents"] > 5000
            ):
                return current, {"status": "amount_not_supported"}
            revision = next_revision(row["revision"])
            row["credit_issued"] = True
            audit = {"event": "credit_issued", "amount_cents": args["amount_cents"]}
            result = {
                "status": "credited",
                "case_id": row["id"],
                "credit_cents": args["amount_cents"],
                "revision": revision,
            }
        else:
            if not row["account_locked"]:
                return current, {"status": "already_unlocked"}
            revision = next_revision(row["revision"])
            row["account_locked"] = False
            audit = {"event": "account_unlocked"}
            result = {"status": "unlocked", "case_id": row["id"], "revision": revision}
    else:
        if all(row[k] == v for k, v in updates.items()):
            return current, {"status": "unchanged"}
        revision = next_revision(row["revision"])
        row.update(deepcopy(updates))
        audit = {"event": "case_updated", "updates": deepcopy(updates)}
        result = {"status": "updated", "case_id": row["id"], "revision": revision}
    row["revision"] = revision
    current["audit"].append(
        {
            "case_id": row["id"],
            **audit,
            "source": deepcopy(observed),
            "revision": revision,
        }
    )
    validate(current)
    return current, result
