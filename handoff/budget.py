"""Hard spend cap. spent + cost > cap means hand off to a human."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass
class BudgetGuard:
    cap: Decimal
    spent: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.cap, Decimal) or not isinstance(self.spent, Decimal):
            raise TypeError("cap and spent must be Decimal")

    def check(self, cost: Decimal) -> Literal["ok", "handoff"]:
        if not isinstance(cost, Decimal):
            raise TypeError("cost must be Decimal")
        return "handoff" if self.spent + cost > self.cap else "ok"

    def shortfall(self, cost: Decimal) -> Decimal:
        return max(Decimal("0"), self.spent + cost - self.cap)

    def charge(self, cost: Decimal) -> None:
        if self.check(cost) == "handoff":
            raise RuntimeError("over cap")
        self.spent += cost

    def raise_cap(self, amount: Decimal) -> None:
        self.cap += amount
