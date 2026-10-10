"""send21 MCP client (live) and a deterministic mock with the same interface.

Verified against github.com/send21io/send21-examples (mcp-payment-request):
- endpoint https://send21.io/mcp, Streamable HTTP, Authorization: Bearer <key>
- tool create_payment_request with fiatCurrency, fiatAmount, options[{currency,network,address,method?}],
  orderId, memo, expiryHours, idempotencyKey (all keys sent, null for optional ones)
- result content[0].text is JSON with id and payPath; pay link = https://send21.io + payPath

UNVERIFIED: argument and response shapes of get_payment_request and get_rate. They are
validated against the live inputSchema at call time instead of being hardcoded.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

SITE_URL = "https://send21.io"
TOOLS = ("create_payment_request", "get_payment_request", "get_rate")


def pay_link(pay_path: str) -> str:
    return urljoin(SITE_URL, pay_path)


def build_create_args(*, amount: Decimal, fiat_currency: str, currency: str, network: str, address: str,
                      order_id: str, memo: str, idempotency_key: str, expiry_hours: int | None = None) -> dict:
    return {
        "fiatCurrency": fiat_currency.upper(),
        "fiatAmount": float(amount),
        "options": [{"currency": currency, "network": network, "address": address}],
        "orderId": order_id,
        "memo": memo,
        "expiryHours": expiry_hours,
        "idempotencyKey": idempotency_key,
    }


def _type_of(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


def validate(value: Any, schema: dict, path: str = "arguments") -> list[str]:
    """Minimal JSON Schema check (type, required, properties, items), ported from the TS example."""
    errors: list[str] = []
    t = schema.get("type")
    if t:
        allowed = t if isinstance(t, list) else [t]
        actual = _type_of(value)
        if not (actual in allowed or (actual == "integer" and "number" in allowed)):
            return [f"{path}: expected {' or '.join(allowed)}, got {actual}"]
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required")
        for key, sub in (schema.get("properties") or {}).items():
            if key in value:
                errors += validate(value[key], sub, f"{path}.{key}")
    if isinstance(value, list) and schema.get("items"):
        for i, item in enumerate(value):
            errors += validate(item, schema["items"], f"{path}[{i}]")
    return errors


class MockSend21Client:
    """No network. Deterministic ids so demo output is identical every run."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._by_key: dict[str, dict] = {}

    def call(self, tool: str, args: dict) -> dict:
        if tool not in TOOLS:
            raise ValueError(f"unknown tool {tool}")
        self.calls.append((tool, args))
        if tool == "create_payment_request":
            key = args["idempotencyKey"]
            if key not in self._by_key:  # same key returns the same request, like the real API
                n = len(self._by_key) + 1
                self._by_key[key] = {"id": f"demo-{n:04d}", "payPath": f"/p/demo-{n:04d}", "status": "mock"}
            return dict(self._by_key[key])
        return {"mock": True, "tool": tool}


class Send21MCPClient:
    """Live client over the send21 MCP server. Sync wrapper around the async MCP SDK."""

    def __init__(self, url: str, api_key: str) -> None:
        self.url, self.api_key = url, api_key

    async def _call(self, tool: str, args: dict) -> dict:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with streamablehttp_client(self.url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name: t for t in (await session.list_tools()).tools}
                if tool not in tools:
                    raise RuntimeError(f"The server does not offer {tool}")
                problems = validate(args, tools[tool].inputSchema or {})
                if problems:
                    raise RuntimeError(f"Arguments do not match the live {tool} schema: {problems}")
                result = await session.call_tool(tool, args)
                text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
                if result.isError:
                    raise RuntimeError(text or f"{tool} failed")
                return json.loads(text)

    def call(self, tool: str, args: dict) -> dict:
        return asyncio.run(self._call(tool, args))
