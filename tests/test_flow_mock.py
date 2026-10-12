import threading
from decimal import Decimal

from adapters.langgraph_agent import Runner
from handoff import mock
from handoff.config import Config
from handoff.send21_client import MockSend21Client, validate
from handoff.store import Store
from handoff.webhook import make_server

KEYS = {"fiatCurrency", "fiatAmount", "options", "orderId", "memo", "expiryHours", "idempotencyKey", "declineNote"}


def setup(monkeypatch):
    for k in ["SEND21_WEBHOOK_SECRET", "BUDGET_CAP", "BUDGET_SPENT", "PAYEE_ADDRESS"]:
        monkeypatch.delenv(k, raising=False)
    cfg = Config.from_env("mock")
    store, client, lines = Store(":memory:"), MockSend21Client(), []
    runner = Runner(cfg, store, client, log=lines.append)
    return cfg, store, client, runner, lines


def test_interrupts_at_handoff(monkeypatch):
    _, store, client, runner, lines = setup(monkeypatch)
    runner.start("run-demo")
    run = store.get_run("run-demo")
    assert run["status"] == "paused"
    assert run["pay_link"] == "https://send21.io/p/demo-0001"
    assert run["order_id"] == "run-demo-step-4"
    assert "[paused] waiting for human signature" in lines
    tool, args = client.calls[0]
    assert tool == "create_payment_request" and set(args) == KEYS
    assert args["fiatAmount"] == 1.5 and args["expiryHours"] is None
    assert args["declineNote"].startswith("The agent stops before step")
    assert args["options"][0] == {"currency": "Usdc", "network": "Base", "address": "<mock-payee-address>"}


def test_full_flow_over_http(monkeypatch):
    cfg, store, client, runner, lines = setup(monkeypatch)
    server = make_server(store, cfg.webhook_secret, runner.resume, port=0, log=lines.append)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        runner.start("run-demo")
        run = store.get_run("run-demo")
        raw = mock.confirmed_body(run["order_id"], run["payment_request_id"], 1.5)
        h = mock.signed_headers(raw, cfg.webhook_secret, "draft.confirmed")
        assert mock.post(raw, h, port)[0] == 200
        assert runner.finished.wait(10)
        run = store.get_run("run-demo")
        assert run["status"] == "done"
        assert Decimal(run["cap"]) == Decimal("6.50") and Decimal(run["spent"]) == Decimal("6.50")
        # a replayed webhook does not run anything again
        assert mock.post(raw, h, port)[1]["outcome"] == "duplicate"
        assert len(client.calls) == 1
        assert lines.count("[agent] task complete") == 1
    finally:
        server.shutdown()


def test_mock_idempotency_key_reuse():
    c = MockSend21Client()
    a = c.call("create_payment_request", {"idempotencyKey": "k"})
    b = c.call("create_payment_request", {"idempotencyKey": "k"})
    assert a == b


def test_validate_against_schema():
    schema = {"type": "object", "required": ["fiatAmount", "options"],
              "properties": {"fiatAmount": {"type": "number"}, "options": {"type": "array", "items": {
                  "type": "object", "required": ["currency", "network", "address"]}}}}
    assert validate({"fiatAmount": 1, "options": [{"currency": "Usdc", "network": "Base", "address": "x"}]}, schema) == []
    assert validate({"fiatAmount": "1", "options": [{}]}, schema)
