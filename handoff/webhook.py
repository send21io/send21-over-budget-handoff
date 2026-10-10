"""send21 webhook receiver.

Verified against github.com/send21io/send21-examples (webhook-receiver/verify.js, server.js):
- X-Send21-Signature: sha256=<lowercase hex HMAC-SHA256 of the raw body, keyed with the signing secret>
- X-Send21-Event (event name), X-Send21-Delivery (delivery id, reused on retries)
- no timestamp header is documented, so there is no timestamp window
- any 2xx counts as delivered; deliveries can be retried and arrive out of order
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .store import Store

SIGNATURE_HEADER = "X-Send21-Signature"
EVENT_HEADER = "X-Send21-Event"
DELIVERY_HEADER = "X-Send21-Delivery"
MAX_BODY_BYTES = 1024 * 1024
PATH = "/webhooks/send21"

ResumeFn = Callable[[str, dict], None]


def sign(raw: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def verify_signature(raw: bytes, header: str | None, secret: str) -> bool:
    if not secret or not isinstance(header, str):
        return False
    return hmac.compare_digest(sign(raw, secret).encode(), header.encode())


def handle_delivery(store: Store, raw: bytes, headers: dict, secret: str,
                    resume: ResumeFn, log: Callable[[str], None] = print) -> tuple[int, dict]:
    h = {k.lower(): v for k, v in headers.items()}
    if not verify_signature(raw, h.get(SIGNATURE_HEADER.lower()), secret):
        log("[webhook] rejected: missing or invalid signature")
        return 401, {"error": "invalid signature"}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:
        return 400, {"error": "invalid json"}
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), str):
        return 400, {"error": "missing event"}

    event, data = payload["event"], payload.get("data") or {}
    delivery = h.get(DELIVERY_HEADER.lower())
    if isinstance(delivery, str) and store.seen_delivery(delivery):
        log(f"[webhook] duplicate delivery {delivery}, already handled")
        return 200, {"ok": True, "outcome": "duplicate"}

    outcome = "ignored"
    run = store.run_by_order(data.get("orderId", "")) if data.get("orderId") else None
    if event in ("draft.confirmed", "draft.amount_mismatch", "draft.seen"):
        if run is None:
            log(f"[webhook] {event} for unknown orderId {data.get('orderId')!r}, ignored")
            outcome = "unknown_order"
        elif run["payment_request_id"] and data.get("paymentRequestId") not in (None, run["payment_request_id"]):
            log(f"[webhook] {event} paymentRequestId does not match run {run['run_id']}, ignored")
            outcome = "request_mismatch"
        elif event == "draft.confirmed":
            store.add_event(run["run_id"], event, payload)
            if store.advance(run["run_id"], "confirmed"):
                if data.get("receivedAmount") is not None or data.get("receivedAmountSats") is not None:
                    log(f"[webhook] owner accepted a different amount: {data.get('receivedAmount')}")
                log("[webhook] draft.confirmed (HMAC verified)")
                outcome = "resume"
            else:
                outcome = "already_handled"
        elif event == "draft.amount_mismatch":
            store.add_event(run["run_id"], event, payload)
            store.advance(run["run_id"], "needs_review")
            log("[webhook] draft.amount_mismatch: needs human review, not resuming")
            outcome = "needs_review"
        else:
            log("[webhook] draft.seen: payment seen, waiting for confirmation")
            outcome = "payment_seen"
    else:
        log(f"[webhook] ignored event {event}")

    if isinstance(delivery, str):
        store.add_delivery(delivery, event)
    if outcome == "resume":
        # Reply first, resume after (send21 waits at most 15 s for a reply).
        threading.Thread(target=resume, args=(run["run_id"], data), daemon=True).start()
    return 200, {"ok": True, "outcome": outcome}


def make_server(store: Store, secret: str, resume: ResumeFn, port: int = 3000,
                host: str = "127.0.0.1", log: Callable[[str], None] = print) -> ThreadingHTTPServer:
    if not secret:
        raise ValueError("A webhook secret is required (SEND21_WEBHOOK_SECRET).")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _reply(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != PATH:
                return self._reply(404, {"error": "not found"})
            length = int(self.headers.get("content-length") or 0)
            if length > MAX_BODY_BYTES:
                return self._reply(413, {"error": "body too large"})
            raw = self.rfile.read(length)
            status, body = handle_delivery(store, raw, dict(self.headers), secret, resume, log)
            self._reply(status, body)

    return ThreadingHTTPServer((host, port), Handler)
