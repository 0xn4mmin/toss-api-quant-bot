"""스케줄러 — 실행창(자정 넘김·주 1회) + 사이클이 caps/gate를 관통하는 통합."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from quantbot.adapter.fills import CostModel
from quantbot.engine import caps
from quantbot.engine.gate import Gate, PaperPortfolio
from quantbot.engine.invariants import load_invariants
from quantbot.engine.scheduler import (
    ExecutionWindow,
    is_rebalance_due,
    run_rebalance_cycle,
    week_key,
)

INV = load_invariants()


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_window_parse_and_midnight_crossing():
    w = ExecutionWindow.parse("23:00-00:30 KST")
    assert w.contains(_utc("2026-07-06T14:30:00"))   # 23:30 KST — 창 안
    assert w.contains(_utc("2026-07-06T15:10:00"))   # 00:10 KST(+1일) — 자정 넘김
    assert not w.contains(_utc("2026-07-06T10:00:00"))  # 19:00 KST — 밖
    with pytest.raises(Exception):
        ExecutionWindow.parse("nonsense")


def test_due_once_per_week():
    w = ExecutionWindow.parse("09:05-09:30 KST")
    now = _utc("2026-07-07T00:10:00")                # 화 09:10 KST
    assert is_rebalance_due(now, w, last_done_week=None)
    done = week_key(now, w)
    assert not is_rebalance_due(now, w, last_done_week=done)   # 같은 주 재실행 금지
    next_week = _utc("2026-07-14T00:10:00")
    assert is_rebalance_due(next_week, w, last_done_week=done)


def test_cycle_routes_everything_through_caps_and_gate(registry):
    """통합: 목표 비중 → 밴드 → caps → gate — 페이퍼 체결과 거부가 함께 나온다."""
    costs = CostModel(0.001, 100.0, 0.002, 0.0, 0.0, 0.0)
    paper = PaperPortfolio(cash=5_000_000.0)
    prices = {"AAA": 50_000.0, "BBB": 50_000.0, "CCC": 50_000.0}
    gate = Gate(registry, costs, live_trading=False, paper=paper,
                quotes=lambda s: prices[s], receipt_ttl_s=300.0, clock=lambda: 0.0)
    state = caps.CapsState()
    state.start_day(5_000_000.0)

    result = run_rebalance_cycle(
        inv=INV, caps_state=state, gate=gate,
        target_weights={"AAA": 0.10, "BBB": 0.005, "CCC": 0.18},  # CCC 18% — INV-01 위반
        current_weights={},
        band=0.02,                       # BBB(0.5%)는 밴드 안 — 주문 생략
        equity_krw=5_000_000.0, cash_krw=5_000_000.0,
        position_value_krw={}, prices_krw=prices,
    )
    assert result.cleared == 1                      # AAA만
    assert paper.qty.get("AAA") and "CCC" not in paper.qty
    assert any("INV-01" in reason for _, reason in result.rejected)
    assert state.daily_order_count == 1
    assert len(registry.rows("orders")) == 1        # 게이트가 기록한 페이퍼 체결


def test_cycle_fails_closed_without_price(registry):
    costs = CostModel(0.001, 100.0, 0.002, 0.0, 0.0, 0.0)
    gate = Gate(registry, costs, live_trading=False,
                paper=PaperPortfolio(cash=1.0), quotes=lambda s: 1.0,
                receipt_ttl_s=300.0, clock=lambda: 0.0)
    state = caps.CapsState()
    state.start_day(1.0)
    with pytest.raises(Exception, match="시세 없음"):
        run_rebalance_cycle(
            inv=INV, caps_state=state, gate=gate,
            target_weights={"GHOST": 0.1}, current_weights={}, band=0.0,
            equity_krw=5_000_000.0, cash_krw=5_000_000.0,
            position_value_krw={}, prices_krw={},
        )