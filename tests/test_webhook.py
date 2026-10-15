import json
import threading
from pathlib import Path

import pytest

from handoff import mock
from handoff.store import Store
from handoff.webhook import make_server, sign

SECRET = "whsec_test"
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def env():
    store = Store(":memory:")
    store.upsert_run("run-demo", order_id="run-demo-step-4", status="paused", payment_request_id="demo-0001",
                     pending_cost="1.50", cap="5.00", spent="4.20", currency="USD")
    resumed = []
    done = threading.Event()

    def resume(run_id, data):
        resumed.append((run_id, data))
        done.set()

    server = make_server(store, SECRET, resume, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield store, resumed, done, port
    server.shutdown()


def body(name, **data):
    b = json.loads((FIX / name).read_text())
    b["data"].update(data)
    return json.dumps(b).encode()


def test_confirmed_resumes(env):
    store, resumed, done, port = env
    raw = body("draft_confirmed.json")
    status, res = mock.post(raw, mock.signed_headers(raw, SECRET, "draft.confirmed"), port)
    assert (status, res["outcome"]) == (200, "resume")
    assert done.wait(5)
    assert resumed[0][0] == "run-demo"
    assert store.get_run("run-demo")["status"] == "confirmed"


def test_bad_signature(env):  # V2 and legacy both signed with the wrong secret
    store, resumed, _, port = env
    raw = body("draft_confirmed.json")
    h = mock.signed_headers(raw, "wrong", "draft.confirmed")
    assert mock.post(raw, h, port)[0] == 401
    assert store.get_run("run-demo")["status"] == "paused" and not resumed


def test_duplicate_delivery(env):
    _, resumed, done, port = env
    raw = body("draft_confirmed.json")
    h = mock.signed_headers(raw, SECRET, "draft.confirmed", delivery_id="d-1")
    assert mock.post(raw, h, port)[1]["outcome"] == "resume"
    assert mock.post(raw, h, port)[1]["outcome"] == "duplicate"
    done.wait(5)
    assert len(resumed) == 1


def test_second_confirmed_new_delivery_is_noop(env):
    _, resumed, done, port = env
    raw = body("draft_confirmed.json")
    mock.post(raw, mock.signed_headers(raw, SECRET, "draft.confirmed"), port)
    assert mock.post(raw, mock.signed_headers(raw, SECRET, "draft.confirmed"), port)[1]["outcome"] == "already_handled"
    done.wait(5)
    assert len(resumed) == 1


def test_unknown_order(env):
    _, resumed, _, port = env
    raw = body("draft_confirmed.json", orderId="nope")
    assert mock.post(raw, mock.signed_headers(raw, SECRET, "draft.confirmed"), port)[1]["outcome"] == "unknown_order"
    assert not resumed


def test_request_id_mismatch(env):
    _, resumed, _, port = env
    raw = body("draft_confirmed.json", paymentRequestId="other")
    assert mock.post(raw, mock.signed_headers(raw, SECRET, "draft.confirmed"), port)[1]["outcome"] == "request_mismatch"
    assert not resumed


def test_amount_mismatch_needs_review(env):
    store, resumed, _, port = env
    raw = body("draft_amount_mismatch.json")
    assert mock.post(raw, mock.signed_headers(raw, SECRET, "draft.amount_mismatch"), port)[1]["outcome"] == "needs_review"
    assert store.get_run("run-demo")["status"] == "needs_review" and not resumed


def test_seen_does_not_resume(env):
    _, resumed, _, port = env
    raw = body("draft_confirmed.json")
    raw = raw.replace(b"draft.confirmed", b"draft.seen")
    assert mock.post(raw, mock.signed_headers(raw, SECRET, "draft.seen"), port)[1]["outcome"] == "payment_seen"
    assert not resumed


def test_invalid_json_and_missing_event(env):
    *_, port = env
    raw = b"not json"
    assert mock.post(raw, {"X-Send21-Signature": sign(raw, SECRET)}, port)[0] == 400
    raw = b'{"data":{}}'
    assert mock.post(raw, {"X-Send21-Signature": sign(raw, SECRET)}, port)[0] == 400


def test_too_large(env):
    *_, port = env
    raw = b"x" * (1024 * 1024 + 1)
    try:
        status = mock.post(raw, {"X-Send21-Signature": sign(raw, SECRET)}, port)[0]
    except Exception:  # server may close the connection early
        status = 413
    assert status == 413


# --- X-Send21-Signature-V2 -------------------------------------------------

import hashlib as _hashlib
import hmac as _hmac
import time as _time

from handoff.webhook import sign_v2, verify_delivery, verify_signature_v2


def test_sign_v2_documented_format():
    raw, t = b'{"event":"test"}', 1792046712
    mac = _hmac.new(SECRET.encode(), f"{t}.d-1.".encode() + raw, _hashlib.sha256).hexdigest()
    assert sign_v2(raw, "d-1", t, SECRET) == f"t={t},v1={mac}"


def test_v2_window_and_tampering():
    raw, now = b'{"event":"test"}', int(_time.time())
    assert verify_signature_v2(raw, sign_v2(raw, "d-1", now, SECRET), "d-1", SECRET)
    assert verify_signature_v2(raw, sign_v2(raw, "d-1", now - 299, SECRET), "d-1", SECRET, now=now)
    assert not verify_signature_v2(raw, sign_v2(raw, "d-1", now - 301, SECRET), "d-1", SECRET, now=now)
    assert not verify_signature_v2(raw, sign_v2(raw, "d-1", now + 301, SECRET), "d-1", SECRET, now=now)
    assert not verify_signature_v2(raw, sign_v2(raw, "d-1", now, SECRET), "d-2", SECRET)
    assert not verify_signature_v2(raw + b" ", sign_v2(raw, "d-1", now, SECRET), "d-1", SECRET)
    assert not verify_signature_v2(raw, sign_v2(raw, "d-1", now, "wrong"), "d-1", SECRET)
    for h in (None, "", "t=,v1=", f"t={now}", "t=abc,v1=00", ["x"]):
        assert not verify_signature_v2(raw, h, "d-1", SECRET)


def test_verify_delivery_prefers_v2():
    raw, now = b'{"event":"test"}', int(_time.time())
    stale = sign_v2(raw, "d-1", now - 600, SECRET)
    legacy = sign(raw, SECRET)
    assert verify_delivery(raw, {"X-Send21-Signature-V2": sign_v2(raw, "d-1", now, SECRET),
                                 "X-Send21-Delivery": "d-1", "X-Send21-Signature": legacy}, SECRET) == (True, "v2")
    # a stale V2 is refused even with a valid legacy signature next to it
    assert verify_delivery(raw, {"X-Send21-Signature-V2": stale, "X-Send21-Delivery": "d-1",
                                 "X-Send21-Signature": legacy}, SECRET) == (False, "v2")
    assert verify_delivery(raw, {"X-Send21-Signature": legacy}, SECRET) == (True, "v1")
    assert verify_delivery(raw, {"X-Send21-Signature": legacy}, SECRET, allow_legacy=False) == (False, None)


def test_http_v2_only_and_stale(env):
    store, resumed, done, port = env
    raw = body("draft_confirmed.json")
    stale = mock.signed_headers(raw, SECRET, "draft.confirmed", timestamp=int(_time.time()) - 600)
    assert mock.post(raw, stale, port)[0] == 401
    h = mock.signed_headers(raw, SECRET, "draft.confirmed", legacy=False)
    status, res = mock.post(raw, h, port)
    assert (status, res["outcome"]) == (200, "resume")
    assert done.wait(5)


def test_http_legacy_fallback(env):
    store, resumed, done, port = env
    raw = body("draft_confirmed.json")
    h = mock.signed_headers(raw, SECRET, "draft.confirmed", v2=False)
    assert mock.post(raw, h, port)[1]["outcome"] == "resume"
