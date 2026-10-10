#!/usr/bin/env python3
"""Sign a draft.confirmed body with SEND21_WEBHOOK_SECRET and POST it to a running receiver.

  python scripts/send_mock_webhook.py --order-id run-demo-step-4 --request-id demo-0001 --amount 1.50
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handoff import mock  # noqa: E402
from handoff.config import MOCK_WEBHOOK_SECRET  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--order-id", required=True)
p.add_argument("--request-id", default="demo-0001")
p.add_argument("--amount", type=float, default=1.50)
p.add_argument("--currency", default="USD")
p.add_argument("--port", type=int, default=int(os.environ.get("WEBHOOK_PORT", "3000")))
a = p.parse_args()
raw = mock.confirmed_body(a.order_id, a.request_id, a.amount, a.currency)
secret = os.environ.get("SEND21_WEBHOOK_SECRET", MOCK_WEBHOOK_SECRET)
print(mock.post(raw, mock.signed_headers(raw, secret, "draft.confirmed"), a.port))
