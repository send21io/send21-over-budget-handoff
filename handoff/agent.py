"""Framework-agnostic task loop with a hard budget and a human handoff.

The agent never moves funds. When a step would exceed the cap, it asks send21 to
prepare a payment request (create_payment_request), returns the pay link and pauses.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from .budget import BudgetGuard
from .config import Config
from .send21_client import build_create_args, pay_link
from .store import Store


@dataclass
class Step:
    name: str
    cost: Decimal


DEMO_STEPS = [
    Step("fetch sources", Decimal("1.40")),
    Step("summarize", Decimal("1.40")),
    Step("draft sections", Decimal("1.40")),
    Step("render report", Decimal("1.50")),
    Step("final check", Decimal("0.80")),
]


@dataclass
class Handoff:
    order_id: str
    payment_request_id: str
    pay_link: str
    amount: Decimal


def run_steps(guard: BudgetGuard, steps: list[Step], start: int, log: Callable[[str], None]) -> int | None:
    """Run steps from index start. Returns the index of the step that needs a handoff, or None when done."""
    for i in range(start, len(steps)):
        step = steps[i]
        if guard.check(step.cost) == "handoff":
            log(f"[agent] next: step {i + 1} '{step.name}', cost {step.cost} {guard.currency}")
            log(f"[budget] {guard.spent} + {step.cost} = {guard.spent + step.cost} > {guard.cap}  OVER CAP")
            return i
        guard.charge(step.cost)
        log(f"[agent] step {i + 1} '{step.name}' done, spent {guard.spent} / {guard.cap} {guard.currency}")
    return None


def request_handoff(*, cfg: Config, store: Store, client, run_id: str, guard: BudgetGuard, step_index: int,
                    step: Step, log: Callable[[str], None]) -> Handoff:
    """Create (or reuse) the payment request for this run and step. Safe to call twice."""
    order_id = f"{run_id}-step-{step_index + 1}"
    run = store.get_run(run_id) or {}
    if run.get("order_id") == order_id and run.get("payment_request_id"):
        return Handoff(order_id, run["payment_request_id"], run["pay_link"], Decimal(run["pending_cost"]))
    amount = step.cost if cfg.amount_mode == "cost" else guard.shortfall(step.cost)
    key = run.get("idempotency_key") if run.get("order_id") == order_id and run.get("idempotency_key") else str(uuid.uuid4())
    # Persist the idempotency key before calling, so a retry after a crash reuses it.
    store.upsert_run(run_id, order_id=order_id, status="running", cap=str(guard.cap), spent=str(guard.spent),
                     pending_cost=str(amount), currency=guard.currency, idempotency_key=key)
    args = build_create_args(
        amount=amount, fiat_currency=guard.currency, currency=cfg.payee_currency, network=cfg.payee_network,
        address=cfg.payee_address, order_id=order_id, idempotency_key=key,
        memo=f"Agent over budget: step '{step.name}' needs {amount} {guard.currency} "
             f"(spent {guard.spent} of {guard.cap})",
        # Tells the human what "no" means, so declining is an informed choice.
        decline_note=f"The agent stops before step '{step.name}' and the run ends without it."[:200],
    )
    log(f"[send21] create_payment_request fiatAmount={amount} {guard.currency}")
    log(f"         orderId={order_id} options=[{cfg.payee_currency}/{cfg.payee_network}]")
    res = client.call("create_payment_request", args)
    if not res.get("payPath"):
        raise RuntimeError(f"No payPath in the response: {res}")
    link = pay_link(res["payPath"])
    store.upsert_run(run_id, payment_request_id=res.get("id"), pay_link=link)
    store.advance(run_id, "paused")
    log("Handoff: drafted payment request")
    log(f"  pay link: {link}")
    return Handoff(order_id, res.get("id"), link, amount)
