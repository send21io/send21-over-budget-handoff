"""Mock mode: build a draft.confirmed body and post it signed with the local secret."""
from __future__ import annotations

import json
import urllib.request
import uuid
from pathlib import Path

from .webhook import DELIVERY_HEADER, EVENT_HEADER, PATH, SIGNATURE_HEADER, sign

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def confirmed_body(order_id: str, payment_request_id: str, fiat_amount: float, fiat_currency: str = "USD") -> bytes:
    base = json.loads((FIXTURES / "draft_confirmed.json").read_text())
    base["data"].update(orderId=order_id, paymentRequestId=payment_request_id,
                        fiatAmount=fiat_amount, fiatCurrency=fiat_currency)
    return json.dumps(base).encode()


def signed_headers(raw: bytes, secret: str, event: str, delivery_id: str | None = None) -> dict:
    return {
        "content-type": "application/json",
        SIGNATURE_HEADER: sign(raw, secret),
        EVENT_HEADER: event,
        DELIVERY_HEADER: delivery_id or str(uuid.uuid4()),
    }


def post(raw: bytes, headers: dict, port: int) -> tuple[int, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{PATH}", data=raw, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
