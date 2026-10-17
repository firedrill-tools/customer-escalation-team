"""Actual Agents/OpenAI Responses SDKs over one owned public HTTPX2 transport."""

import asyncio
import base64
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx2

from .completion import MAX_RESPONSE_BYTES, completed, core, usage_of
from .evidence import error_info
from .strict import canonical, parse_json, sha256, text


@dataclass(frozen=True)
class Config:
    api_key: str
    model: str
    base_url: str


def config(api_key=None, model=None, base_url=None):
    key = os.environ.get("OPENAI_API_KEY") if api_key is None else api_key
    selected = os.environ.get("ESCALATION_MODEL") if model is None else model
    if (
        type(key) is not str
        or not 1 <= len(key) <= 1024
        or any(ord(c) < 33 or ord(c) > 126 for c in key)
    ):
        raise ValueError("explicit OPENAI_API_KEY required")
    if type(selected) is not str or not re.fullmatch(
        r"[a-z][a-z0-9._-]{1,127}", selected
    ):
        raise ValueError("explicit enabled ESCALATION_MODEL identifier required")
    origin = "https://api.openai.com/v1" if base_url is None else base_url
    if type(origin) is not str:
        raise ValueError("explicit provider origin required")
    parsed = urlsplit(origin)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/", "/v1", "/v1/")
        or parsed.hostname is None
        or parsed.scheme not in ("http", "https")
        or parsed.scheme == "http"
        and (parsed.hostname != "127.0.0.1" or parsed.port is None)
    ):
        raise ValueError("HTTPS or explicit owned loopback Responses origin required")
    if os.environ.get("OPENAI_CUSTOM_HEADERS"):
        raise ValueError("ambient OPENAI_CUSTOM_HEADERS are unsupported")
    return Config(
        key, selected, origin.rstrip("/") + ("/v1" if parsed.path in ("", "/") else "")
    )


class NativeTransport(httpx2.AsyncBaseTransport):
    def __init__(self, native):
        self.native = native
        self.owner = native.owner
        self.close_attempted = False
        self.closed = False
        self.inner = httpx2.AsyncHTTPTransport(
            trust_env=False,
            retries=0,
            limits=httpx2.Limits(max_connections=1, max_keepalive_connections=1),
        )

    async def handle_async_request(self, request):
        self.owner.check()
        pending = self.native.pending
        if (
            pending is None
            or pending["sent"]
            or len(self.owner.receipt.data["calls"]) >= 16
        ):
            raise ValueError(
                "one prepared Responses request per bounded SDK turn required"
            )
        raw = await request.aread()
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("original Responses request exceeds2MiB")
        body = parse_json(raw)
        configuration = self.native.config
        if (
            request.method != "POST"
            or str(request.url) != configuration.base_url + "/responses"
            or request.headers.get("authorization") != "Bearer " + configuration.api_key
        ):
            raise ValueError(
                "exact owned Responses endpoint and authentication required"
            )
        allowed = {
            "host",
            "accept",
            "accept-encoding",
            "connection",
            "user-agent",
            "content-type",
            "content-length",
            "authorization",
            "openai-organization",
            "openai-project",
            "x-client-request-id",
        }
        for name, value in request.headers.items():
            if name not in allowed and not name.startswith("x-stainless-"):
                raise ValueError("unsupported ambient Responses header")
            if name in ("openai-organization", "openai-project") and value:
                raise ValueError("ambient provider account routing refused")
            text(value, 0, 2048)
        if (
            type(body) is not dict
            or set(body) - set(pending["expected"]) not in (set(), {"stream"})
            or body.get("stream", False) is not False
            or {k: v for k, v in body.items() if k != "stream"} != pending["expected"]
        ):
            self.owner.receipt.data.setdefault("rejected_requests", []).append(
                {
                    "request": body,
                    "expected": deepcopy(pending["expected"]),
                    "request_sha256": sha256(raw),
                    "sent": False,
                }
            )
            self.owner.save()
            raise ValueError("exact frozen SDK role/input/tools/settings required")
        pending["sent"] = True
        call = {
            "ordinal": len(self.owner.receipt.data["calls"]) + 1,
            "role": pending["role"],
            "url": str(request.url),
            "method": "POST",
            "key_present": True,
            "request_base64": base64.b64encode(raw).decode(),
            "request_sha256": sha256(raw),
            "request": body,
            "response": None,
            "response_complete": False,
            "error": None,
        }
        self.owner.receipt.data["calls"].append(call)
        self.owner.save()
        response, phase, chunks, size = None, None, [], 0
        try:
            phase = asyncio.get_running_loop().call_later(
                30,
                lambda: self.owner.cancel(
                    TimeoutError("escalation provider phase exceeded30seconds")
                ),
            )
            self.owner.check()
            response = await self.inner.handle_async_request(request)
            call["status_code"] = response.status_code
            call["response_headers"] = [
                list(pair) for pair in response.headers.multi_items()
            ]
            async for chunk in response.aiter_bytes():
                take = min(len(chunk), MAX_RESPONSE_BYTES - size)
                chunks.append(chunk[:take])
                size += take
                if take != len(chunk):
                    raise ValueError("original Responses body exceeds1MiB")
            call["response_complete"] = True
            original_raw = b"".join(chunks)
            original = parse_json(original_raw)
            call["response"] = original
            call["reported_usage"] = usage_of(original)
            if response.status_code != 200:
                raise ValueError("original Responses HTTP status is not200")
            _body, completion = completed(original_raw, pending["names"])
            if (
                completion["response_id"] in self.native.response_ids
                or any(item in self.native.item_ids for item in completion["item_ids"])
                or any(
                    c["call_id"] in self.native.call_ids for c in completion["calls"]
                )
            ):
                raise ValueError("original response/item/tool-call identity repeated")
            self.native.response_ids.add(completion["response_id"])
            self.native.item_ids.update(completion["item_ids"])
            self.native.call_ids.update(c["call_id"] for c in completion["calls"])
            call["completion"] = completion
            self.owner.check()
            self.owner.save()
            return httpx2.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "content-length": str(len(original_raw)),
                },
                content=original_raw,
                request=request,
            )
        except BaseException as error:
            call["error"] = error_info(error)
            self.owner.fail(error)
            raise
        finally:
            if phase is not None:
                phase.cancel()
            captured = b"".join(chunks)
            call.update(
                response_base64=base64.b64encode(captured).decode(),
                response_sha256=sha256(captured),
                captured_bytes=len(captured),
            )
            if response is not None:
                try:
                    await response.aclose()
                    call["response_closed"] = True
                except BaseException as error:
                    call["response_closed"] = False
                    self.owner.fail(error)
                    raise
            self.owner.save()

    async def aclose(self):
        if self.close_attempted:
            return
        self.close_attempted = True
        await self.inner.aclose()
        self.closed = True


class Native:
    def __init__(self, owner, configuration):
        self.owner, self.config = owner, configuration
        self.pending = None
        self.response_ids, self.item_ids, self.call_ids, self.consumed_calls = (
            set(),
            set(),
            set(),
            set(),
        )
        self.transport = self.http = self.client = self.model = None
        self.close_attempted = self.closed = False
        try:
            from agents import OpenAIResponsesModel
            from openai import AsyncOpenAI

            self.transport = NativeTransport(self)
            self.http = httpx2.AsyncClient(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx2.Timeout(300),
            )
            self.client = AsyncOpenAI(
                api_key=configuration.api_key,
                admin_api_key="",
                organization="",
                project="",
                webhook_secret="",
                base_url=configuration.base_url,
                http_client=self.http,
                max_retries=0,
                timeout=300,
            )
            self.model = OpenAIResponsesModel(configuration.model, self.client)
        except BaseException as error:
            error.native_owner = self
            raise

    def prepare(self, role, agent, system_prompt, input_items):
        self.owner.check()
        functions = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": deepcopy(tool.params_json_schema),
                "strict": tool.strict_json_schema,
            }
            for tool in agent.tools
        ]
        functions.extend(
            {
                "type": "function",
                "name": item.tool_name,
                "description": item.tool_description,
                "parameters": deepcopy(item.input_json_schema),
                "strict": item.strict_json_schema,
            }
            for item in agent.handoffs
        )
        self.pending = {
            "sent": False,
            "role": role,
            "names": {item["name"] for item in functions},
            "expected": {
                "model": self.config.model,
                "instructions": system_prompt,
                "input": deepcopy(input_items),
                "include": [],
                "tools": functions,
                "max_output_tokens": 1024,
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "store": False,
                "truncation": "disabled",
            },
        }

    def verified_sdk(self, response):
        self.owner.check()
        call = self.owner.receipt.data["calls"][-1]
        normalized = [
            item.model_dump(mode="json", exclude_none=True) for item in response.output
        ]
        usage = response.usage
        call["sdk_response"] = {
            "response_id": response.response_id,
            "output": normalized,
            "usage": {
                field: getattr(usage, field, None)
                for field in (
                    "requests",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                )
            },
        }
        if (
            response.response_id != call["completion"]["response_id"]
            or [core(item) for item in normalized] != call["completion"]["output_core"]
        ):
            raise ValueError(
                "SDK normalization differs from complete original Responses items"
            )
        call["sdk_verified"] = True
        self.owner.save()

    def consume(self, name, arguments, call_id=None):
        self.owner.check()
        call = self.owner.receipt.data["calls"][-1]
        if not call.get("sdk_verified") or len(call["completion"]["calls"]) != 1:
            raise ValueError("genuine verified SDK function or handoff required")
        original = call["completion"]["calls"][0]
        if (
            original["name"] != name
            or original["arguments"] != arguments
            or call_id is not None
            and original["call_id"] != call_id
            or original["call_id"] in self.consumed_calls
        ):
            raise ValueError("genuine exact unconsumed original SDK call required")
        self.consumed_calls.add(original["call_id"])
        return original["call_id"]

    async def close(self):
        if self.close_attempted:
            return
        self.close_attempted = True
        if self.client is not None:
            await self.client.close()
        elif self.http is not None:
            await self.http.aclose()
        elif self.transport is not None:
            await self.transport.aclose()
        self.closed = self.transport is None or self.transport.closed
