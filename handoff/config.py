"""Environment configuration. Real environment variables win over .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

MOCK_WEBHOOK_SECRET = "whsec_mock_local"


def load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    mode: str
    api_key: str
    webhook_secret: str
    mcp_url: str
    payee_address: str
    payee_currency: str
    payee_network: str
    cap: Decimal
    spent: Decimal
    currency: str
    amount_mode: str
    port: int
    db_path: str

    @classmethod
    def from_env(cls, mode: str | None = None) -> "Config":
        mode = (mode or os.environ.get("SEND21_MODE", "mock")).lower()
        secret = os.environ.get("SEND21_WEBHOOK_SECRET", "")
        if mode == "mock" and not secret:
            secret = MOCK_WEBHOOK_SECRET
        cfg = cls(
            mode=mode,
            api_key=os.environ.get("SEND21_API_KEY", ""),
            webhook_secret=secret,
            mcp_url=os.environ.get("SEND21_MCP_URL", "https://send21.io/mcp"),
            payee_address=os.environ.get("PAYEE_ADDRESS", "<mock-payee-address>" if mode == "mock" else ""),
            payee_currency=os.environ.get("PAYEE_CURRENCY", "Usdc"),
            payee_network=os.environ.get("PAYEE_NETWORK", "Base"),
            cap=Decimal(os.environ.get("BUDGET_CAP", "5.00")),
            spent=Decimal(os.environ.get("BUDGET_SPENT", "4.20")),
            currency=os.environ.get("BUDGET_CURRENCY", "USD"),
            amount_mode=os.environ.get("HANDOFF_AMOUNT_MODE", "cost"),
            port=int(os.environ.get("WEBHOOK_PORT", "3000")),
            db_path=os.environ.get("HANDOFF_DB", "state/handoff.sqlite"),
        )
        if mode == "live":
            missing = [n for n, v in [("SEND21_API_KEY", cfg.api_key), ("SEND21_WEBHOOK_SECRET", cfg.webhook_secret),
                                      ("PAYEE_ADDRESS", cfg.payee_address)] if not v]
            if missing:
                raise SystemExit(f"Live mode needs: {', '.join(missing)}")
        return cfg
