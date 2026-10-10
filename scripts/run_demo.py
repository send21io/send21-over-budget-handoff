#!/usr/bin/env python3
"""Run the over-budget handoff demo.

  python scripts/run_demo.py --mock            # default, no network, fake webhook signed locally
  python scripts/run_demo.py --live            # real send21 MCP + real webhooks (see README)
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.langgraph_agent import Runner  # noqa: E402
from handoff import mock  # noqa: E402
from handoff.config import Config, load_dotenv  # noqa: E402
from handoff.send21_client import MockSend21Client, Send21MCPClient  # noqa: E402
from handoff.store import Store  # noqa: E402
from handoff.webhook import make_server  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    m = p.add_mutually_exclusive_group()
    m.add_argument("--mock", action="store_true")
    m.add_argument("--live", action="store_true")
    p.add_argument("--auto-sign", type=float, default=2.0, help="mock: seconds before the fake webhook")
    p.add_argument("--run-id", default=None)
    a = p.parse_args()

    load_dotenv()
    cfg = Config.from_env("live" if a.live else "mock")
    run_id = a.run_id or ("run-demo" if cfg.mode == "mock" else f"run-{int(time.time())}")
    if cfg.mode == "mock":
        print("demo, mock mode: no network calls, no real payments")
        store = Store(":memory:")
        client = MockSend21Client()
    else:
        print("live mode: the send21 key must have the drafts:write scope only. It cannot move funds.")
        store = Store(cfg.db_path)
        client = Send21MCPClient(cfg.mcp_url, cfg.api_key)

    runner = Runner(cfg, store, client)
    server = make_server(store, cfg.webhook_secret, runner.resume, port=cfg.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[webhook] listening on http://127.0.0.1:{cfg.port}/webhooks/send21")
    print(f"[agent] task: compile market report (5 steps)")
    print("[agent] steps 1-3 done")
    print(f"[budget] spent {cfg.spent} / {cfg.cap} {cfg.currency}")

    runner.start(run_id)
    run = store.get_run(run_id)
    if run["status"] != "done":
        if cfg.mode == "mock":
            time.sleep(a.auto_sign)
            print("[human] signs in own wallet (mock)")
            raw = mock.confirmed_body(run["order_id"], run["payment_request_id"], float(run["pending_cost"]),
                                      cfg.currency)
            mock.post(raw, mock.signed_headers(raw, cfg.webhook_secret, "draft.confirmed"), cfg.port)
        else:
            print("Open the pay link and sign in your own wallet. Waiting for draft.confirmed (Ctrl+C to stop).")
        if not runner.finished.wait(timeout=30 if cfg.mode == "mock" else None):
            print("timed out waiting for the webhook")
            return 1
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
