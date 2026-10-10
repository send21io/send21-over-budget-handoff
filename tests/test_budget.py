from decimal import Decimal as D

import pytest

from handoff.budget import BudgetGuard


def test_over_cap_hands_off():
    assert BudgetGuard(D("5.00"), D("4.20")).check(D("1.50")) == "handoff"


def test_exactly_at_cap_is_ok():
    assert BudgetGuard(D("5.00"), D("4.20")).check(D("0.80")) == "ok"


def test_shortfall():
    assert BudgetGuard(D("5.00"), D("4.20")).shortfall(D("1.50")) == D("0.70")
    assert BudgetGuard(D("5.00"), D("1.00")).shortfall(D("1.50")) == D("0")


def test_decimal_only():
    with pytest.raises(TypeError):
        BudgetGuard(5.0, D("1"))
    with pytest.raises(TypeError):
        BudgetGuard(D("5"), D("1")).check(1.5)


def test_charge_refuses_over_cap():
    g = BudgetGuard(D("5.00"), D("4.20"))
    with pytest.raises(RuntimeError):
        g.charge(D("1.50"))
    g.raise_cap(D("1.50"))
    g.charge(D("1.50"))
    assert g.spent == D("5.70")
