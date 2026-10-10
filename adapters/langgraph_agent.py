"""Thin LangGraph adapter: interrupt() at the handoff, Command(resume=...) from the webhook.

This is an example, not an official LangGraph integration.
"""
from __future__ import annotations

import threading
from decimal import Decimal
from typing import Callable, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from handoff.agent import DEMO_STEPS, Step, request_handoff, run_steps
from handoff.budget import BudgetGuard
from handoff.config import Config
from handoff.store import Store


class State(TypedDict):
    run_id: str
    cap: str
    spent: str
    next_step: int
    handoff_step: Optional[int]


def build_graph(cfg: Config, store: Store, client, steps: list[Step], log: Callable[[str], None], checkpointer=None):
    def guard(s: State) -> BudgetGuard:
        return BudgetGuard(Decimal(s["cap"]), Decimal(s["spent"]), cfg.currency)

    def work(s: State):
        g = guard(s)
        idx = run_steps(g, steps, s["next_step"], log)
        return {"cap": str(g.cap), "spent": str(g.spent),
                "next_step": len(steps) if idx is None else idx, "handoff_step": idx}

    def handoff(s: State):
        i = s["handoff_step"]
        request_handoff(cfg=cfg, store=store, client=client, run_id=s["run_id"], guard=guard(s),
                        step_index=i, step=steps[i], log=log)
        log("[paused] waiting for human signature")
        return {}

    def wait(s: State):
        run = store.get_run(s["run_id"])
        data = interrupt({"pay_link": run["pay_link"], "order_id": run["order_id"]})
        # Resumed by a verified draft.confirmed. Raise the cap by the confirmed fiat amount.
        cap = Decimal(s["cap"]) + Decimal(str(data.get("fiatAmount", run["pending_cost"])))
        store.advance(s["run_id"], "resumed")
        store.upsert_run(s["run_id"], cap=str(cap))
        log(f"[agent] resumed, cap now {cap} {cfg.currency}")
        return {"cap": str(cap), "handoff_step": None}

    def finish(s: State):
        store.upsert_run(s["run_id"], spent=s["spent"], cap=s["cap"])
        store.advance(s["run_id"], "done")
        log("[agent] task complete")
        return {}

    g = StateGraph(State)
    g.add_node("work", work)
    g.add_node("handoff", handoff)
    g.add_node("wait", wait)
    g.add_node("finish", finish)
    g.add_edge(START, "work")
    g.add_conditional_edges("work", lambda s: "finish" if s["handoff_step"] is None else "handoff")
    g.add_edge("handoff", "wait")
    g.add_edge("wait", "work")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


class Runner:
    """Starts runs and resumes them when the webhook confirms the payment."""

    def __init__(self, cfg: Config, store: Store, client, steps: list[Step] = DEMO_STEPS,
                 log: Callable[[str], None] = print, checkpointer=None) -> None:
        self.store, self.log, self.cfg = store, log, cfg
        self.graph = build_graph(cfg, store, client, steps, log, checkpointer)
        self._lock = threading.Lock()
        self.finished = threading.Event()

    def _config(self, run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}}

    def start(self, run_id: str, start_step: int = 3) -> dict:
        """start_step: steps before it are already done (the demo starts at spent 4.20 after steps 1-3)."""
        self.store.upsert_run(run_id, status="running", cap=str(self.cfg.cap), spent=str(self.cfg.spent),
                              currency=self.cfg.currency)
        init = {"run_id": run_id, "cap": str(self.cfg.cap), "spent": str(self.cfg.spent),
                "next_step": start_step, "handoff_step": None}
        with self._lock:
            out = self.graph.invoke(init, self._config(run_id))
        self._check_done(run_id)
        return out

    def resume(self, run_id: str, event_data: dict) -> dict:
        with self._lock:
            out = self.graph.invoke(Command(resume=event_data), self._config(run_id))
        self._check_done(run_id)
        return out

    def _check_done(self, run_id: str) -> None:
        if (self.store.get_run(run_id) or {}).get("status") == "done":
            self.finished.set()
