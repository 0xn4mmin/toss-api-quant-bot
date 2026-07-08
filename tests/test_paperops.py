"""연구용 페이퍼 운용 (2026-07-08 결정) — 재시작 복원·월간 선택·주간 방어·기록."""

from __future__ import annotations

import numpy as np
import pytest

from quantbot.adapter.fills import CostModel
from quantbot.engine import caps, paperops
from quantbot.engine.gate import Gate, PaperPortfolio
from quantbot.engine.invariants import load_invariants
from quantbot.strategy.loader import load_strategy

INV = load_invariants()
COSTS = CostModel(0.0001, 1.0, 0.0005, 0.0, 0.0, 0.0)
STRATEGY = load_strategy("strategies/dual-momentum.v3.yaml")
SID = "dual-momentum.v3"
SELECTED = {"lookback_wk": 13, "skip_wk": 0, "top_n": 2}
ETFS = frozenset({"SPY", "EFA", "TLT", "GLD", "IEF"})


def _closes(**growth):
    return {s: 100.0 * np.cumprod(1 + g * np.ones(120)) for s, g in growth.items()}


def _prices(closes):
    return {s: float(c[-1]) for s, c in closes.items()}


def _seed_judgement(registry):
    registry.append_artifact(SID, paperops.ARTIFACT_JUDGEMENT, "sha", {
        "selected_params": SELECTED, "flags": {}, "metrics": {},
        "n_configs_tried": 36,
    })


def _gate(registry, prices):
    return Gate(registry, COSTS, live_trading=False,
                paper=None, quotes=lambda s: prices[s],
                receipt_ttl_s=300.0, clock=lambda: 0.0)


def _run_cycle(registry, portfolio, closes, month_key, state=None):
    prices = _prices(closes)
    gate = Gate(registry, COSTS, live_trading=False, paper=portfolio,
                quotes=lambda s: prices[s], receipt_ttl_s=300.0, clock=lambda: 0.0)
    state = state or caps.CapsState()
    return paperops.run_paper_cycle(
        registry=registry, inv=INV, caps_state=state, gate=gate,
        strategy=STRATEGY, strategy_id=SID, portfolio=portfolio,
        closes=closes, prices_krw=prices, broad_etf_symbols=ETFS,
        selected_params=SELECTED, trading_days_per_week=5,
        trading_days_per_year=252, now_iso="2026-07-08T07:00:00+00:00",
        month_key=month_key,
    )


def test_selected_params_come_from_judgement_artifact(registry):
    with pytest.raises(paperops.PaperOpsError, match="판정 아티팩트"):
        paperops.load_selected_params(registry, SID)  # 판정 없이 페이퍼 없음
    _seed_judgement(registry)
    assert paperops.load_selected_params(registry, SID) == SELECTED


def test_first_cycle_selects_and_fills_with_etf_cap(registry):
    _seed_judgement(registry)
    portfolio = paperops.start_or_resume_session(registry, SID, 5_000_000.0)
    closes = _closes(SPY=0.002, EFA=0.001, TLT=0.0015, GLD=0.0005, IEF=0.0003)
    outcome = _run_cycle(registry, portfolio, closes, "2026-07")
    assert outcome.selection == ["SPY", "TLT"]          # 상위 2 (절대 양수)
    assert outcome.targets["SPY"] <= 0.50 + 1e-9        # INV-01a 캡
    # INV-10 주문 분할: 자산당 250만 → 3조각(각 ≤ 100만), 총 6체결
    assert len(outcome.cycle.fills) == 6 and sorted(portfolio.qty) == ["SPY", "TLT"]
    for f in outcome.cycle.fills:
        assert f["qty"] * f["exec_price"] <= INV.orders.per_order_max_amount_krw + 1e-6
    assert not outcome.cycle.rejected                   # 거부 0 — 전량 집행
    assert outcome.nav_krw == pytest.approx(5_000_000.0, rel=0.01)


def test_restart_rebuilds_portfolio_from_registry(registry):
    """RISK-06: 재시작 = registry 재생 — 현금·수량·평단이 그대로 복원된다."""
    _seed_judgement(registry)
    p1 = paperops.start_or_resume_session(registry, SID, 5_000_000.0)
    closes = _closes(SPY=0.002, EFA=0.001, TLT=0.0015, GLD=0.0005, IEF=0.0003)
    _run_cycle(registry, p1, closes, "2026-07")
    p2 = paperops.start_or_resume_session(registry, SID, 999.0)  # 초기값 무시돼야 함
    assert p2.cash == pytest.approx(p1.cash)
    assert p2.qty == pytest.approx(p1.qty)
    assert p2.avg_cost == pytest.approx(p1.avg_cost)


def test_monthly_selection_weekly_defense(registry):
    """같은 달 = 선택 유지(순위 뒤집혀도), 음전 보유는 주간 사이클이 즉시 이탈."""
    _seed_judgement(registry)
    portfolio = paperops.start_or_resume_session(registry, SID, 5_000_000.0)
    up = _closes(SPY=0.002, EFA=0.001, TLT=0.0015, GLD=0.0005, IEF=0.0003)
    _run_cycle(registry, portfolio, up, "2026-07")
    assert sorted(portfolio.qty) == ["SPY", "TLT"]
    # 같은 달, 순위 뒤집힘(GLD 급등) — 보유 유지 (월간 선택 주기)
    flipped = _closes(SPY=0.0004, EFA=0.001, TLT=0.0015, GLD=0.003, IEF=0.0012)
    o2 = _run_cycle(registry, portfolio, flipped, "2026-07")
    assert o2.selection == ["SPY", "TLT"]
    # 같은 달, SPY 절대 모멘텀 음전 — 주간 방어가 즉시 이탈
    spy_bear = _closes(SPY=-0.001, EFA=0.001, TLT=0.0015, GLD=0.003, IEF=0.0012)
    o3 = _run_cycle(registry, portfolio, spy_bear, "2026-07")
    assert o3.selection == ["TLT"] and "SPY" not in portfolio.qty
    # 새 달 — 재선정 (버퍼 규칙으로 GLD 진입)
    o4 = _run_cycle(registry, portfolio, spy_bear, "2026-08")
    assert "GLD" in o4.selection


def test_all_negative_goes_full_cash(registry):
    _seed_judgement(registry)
    portfolio = paperops.start_or_resume_session(registry, SID, 5_000_000.0)
    up = _closes(SPY=0.002, EFA=0.001, TLT=0.0015, GLD=0.0005, IEF=0.0003)
    _run_cycle(registry, portfolio, up, "2026-07")
    bear = _closes(SPY=-0.002, EFA=-0.001, TLT=-0.0015, GLD=-0.0005, IEF=-0.0003)
    o = _run_cycle(registry, portfolio, bear, "2026-08")
    assert o.selection == [] and o.targets == {}
    assert portfolio.qty == {}                          # 전량 청산 → 현금
    assert portfolio.cash == pytest.approx(o.nav_krw)


def test_cycle_events_are_recorded_for_forward_analysis(registry):
    """순방향 검증의 근거 — NAV·선택·스칼라가 매 사이클 append된다."""
    _seed_judgement(registry)
    portfolio = paperops.start_or_resume_session(registry, SID, 5_000_000.0)
    closes = _closes(SPY=0.002, EFA=0.001, TLT=0.0015, GLD=0.0005, IEF=0.0003)
    _run_cycle(registry, portfolio, closes, "2026-07")
    _run_cycle(registry, portfolio, closes, "2026-07")
    cycles = [e for e in registry.events(paperops.EVENT_PAPER_CYCLE)]
    assert len(cycles) == 2
    p = cycles[0]["payload"]
    assert {"nav_krw", "selection", "scalar", "targets", "month_key"} <= set(p)
    assert cycles[1]["payload"]["selection_due"] is False  # 같은 달 재실행


def test_no_promotion_path_exists(registry):
    """연구 페이퍼는 승격 불가 — LC-G2/INV-08 기록이 없어 자동 승인이 거부된다."""
    from quantbot.engine.approval import approve_switch

    _seed_judgement(registry)
    result = approve_switch(
        STRATEGY, registry, INV,
        universe_symbols={"assets": ["SPY", "TLT"]},
        whitelist={"assets": {"SPY", "TLT"}},
        stock_master={"SPY": ("FOREIGN_ETF", "1.0"), "TLT": ("FOREIGN_ETF", "1.0")},
        current_weights={}, target_weights={"SPY": 0.5},
        broad_etf_symbols=frozenset({"SPY", "TLT"}),
    )
    assert not result.auto_approved
    assert any("LC-G2" in r for r in result.reasons)
    assert any("INV-08" in r for r in result.reasons)