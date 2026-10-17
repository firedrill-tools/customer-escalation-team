"""Whole original Responses completion and strict function arguments before SDK dispatch."""

from .strict import integer, keys, parse_json, text

MAX_RESPONSE_BYTES = 1024 * 1024


def counter(usage, name):
    if type(usage) is not dict or name not in usage:
        return {"status": "unknown", "value": None}
    try:
        integer(usage[name])
    except ValueError:
        return {"status": "invalid", "value": None}
    return {"status": "observed", "value": usage[name]}


def usage_of(body):
    usage = body.get("usage") if type(body) is dict else None
    return {
        "source": "original Responses JSON counters; no estimate or billing proof",
        **{
            name: counter(usage, name)
            for name in ("input_tokens", "output_tokens", "total_tokens")
        },
        "cached_tokens": counter(
            usage.get("input_tokens_details") if type(usage) is dict else None,
            "cached_tokens",
        ),
        "reasoning_tokens": counter(
            usage.get("output_tokens_details") if type(usage) is dict else None,
            "reasoning_tokens",
        ),
        "cost_usd": None,
    }


def core(item):
    fields = (
        ("id", "type", "status", "call_id", "name", "arguments")
        if item["type"] == "function_call"
        else (
            ("id", "type", "role", "status")
            if item["type"] == "message"
            else ("id", "type", "status")
        )
    )
    result = {key: item[key] for key in fields if key in item}
    if item["type"] == "message":
        result["content"] = [
            {key: part[key] for key in ("type", "text") if key in part}
            for part in item["content"]
        ]
    return result


def completed(raw, allowed_names):
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("original Responses JSON exceeds1MiB")
    value = parse_json(raw)
    if (
        type(value) is not dict
        or value.get("object") != "response"
        or value.get("status") != "completed"
        or "error" not in value
        or value["error"] is not None
        or "incomplete_details" not in value
        or value["incomplete_details"] is not None
    ):
        raise ValueError("explicit complete original Responses status required")
    text(value.get("id"), 1, 240)
    text(value.get("model"), 1, 240)
    output = value.get("output")
    if type(output) is not list or not 1 <= len(output) <= 32:
        raise ValueError("bounded complete original Responses output required")
    seen, calls, messages, texts = set(), [], [], []
    for item in output:
        if type(item) is not dict:
            raise ValueError("original Responses item object required")
        identity = text(item.get("id"), 1, 240)
        if identity in seen:
            raise ValueError("original Responses item identity repeated")
        seen.add(identity)
        if item.get("type") in ("function_call", "message"):
            if item.get("status") != "completed":
                raise ValueError("complete original message or function item required")
        if item.get("type") == "function_call":
            name = item.get("name")
            if type(name) is not str or name not in allowed_names:
                raise ValueError(
                    "original function is not registered for the current SDK role"
                )
            call_id = text(item.get("call_id"), 1, 240)
            text(item.get("arguments"), 2, 8192)
            arguments = parse_json(item["arguments"].encode("utf-8", errors="strict"))
            fields = (
                {"expected_revision", "amount_cents"}
                if name == "issue_credit"
                else {"expected_revision"} if name == "unlock_account" else set()
            )
            keys(arguments, fields)
            for field in fields:
                integer(arguments[field], 1)
            calls.append(
                {
                    "name": name,
                    "call_id": call_id,
                    "arguments": arguments,
                    "arguments_json": item["arguments"],
                }
            )
        elif item.get("type") == "message":
            if (
                item.get("role") != "assistant"
                or type(item.get("content")) is not list
                or not 1 <= len(item["content"]) <= 16
            ):
                raise ValueError("complete original assistant message required")
            for part in item["content"]:
                if type(part) is not dict or part.get("type") != "output_text":
                    raise ValueError("bounded original assistant text required")
                text(part.get("text"), 1, 8192)
                texts.append(part["text"])
            messages.append(identity)
        elif item.get("type") == "reasoning":
            if "status" in item and item["status"] != "completed":
                raise ValueError("incomplete original reasoning item refused")
        else:
            raise ValueError("unsupported original Responses output item")
    if len(calls) > 1 or calls and messages or not calls and not messages:
        raise ValueError("one sequential function or final assistant output required")
    summary = "\n".join(texts)
    if messages:
        text(summary, 1, 16384)
        if not summary.strip():
            raise ValueError("nonblank final assistant output required")
    return value, {
        "response_id": value["id"],
        "reported_model": value["model"],
        "output_core": [core(item) for item in output],
        "item_ids": list(seen),
        "calls": calls,
        "final_text": summary if messages else None,
    }
