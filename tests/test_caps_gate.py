"""Phase 4 DoD — 캡 위반은 ClearedIntent가 될 수 없고, preview 없는 execute는
컴파일·실행 양쪽에서 불가능하며, 회전율 초과는 에스컬레이션 + 단계 계획,
INV-07 도달은 전면 거부다."""

from __future__ import annotations

import dataclasses

import pytest

from quantbot.adapter.fills import CostModel
from quantbot.adapter.official import order as official_order
from quantbot.engine import caps
from quantbot.engine.approval import EVENT_PAPER_PASSED, approve_switch
from quantbot.engine.gate import Gate, GateError, PaperPortfolio, PaperReceipt, ReceiptRequired
from quantbot.engine.invariants import load_invariants
from quantbot.strategy.schema import parse_strategy
from test_strategy import _valid_dict

INV = load_invariants()
COSTS = CostModel(
    commission_rate=0.001, min_commission_krw=100.0, slippage_rate=0.002,
    sell_tax_rate=0.0018, annual_gain_tax_rate=0.0, annual_deduction_krw=0.0,
)


def _state(equity: float = 5_000_000.0) -> caps.CapsState:
    s = caps.CapsState()
    s.start_day(equity)
    return s


def _check(intents, state=None, cash=5_000_000.0, positions=None):
    return caps.check(
        intents, INV, state or _state(),
        equity_krw=5_000_000.0, cash_krw=cash,
        position_value_krw=positions or {},
    )


# ── DoD: 캡 위반 의도는 ClearedIntent가 되지 못한다 ─────────────────────


def test_cap_violations_never_become_cleared_intent():
    intents = [
        caps.OrderIntent("AAA", "BUY", quantity=8, est_price_krw=70_000),    # 560k=11.2% 통과
        caps.OrderIntent("BBB", "BUY", amount_krw=1_500_000),                # INV-10
        caps.OrderIntent("CCC", "BUY", quantity=10, est_price_krw=65_000),   # INV-01 (13%)
        caps.OrderIntent("DDD", "BUY", amount_krw=500_000),                  # 10% 통과
    ]
    d = _check(intents)
    cleared_symbols = {c.symbol for c in d.cleared}
    assert cleared_symbols == {"AAA", "DDD"}
    reasons = {i.symbol: r for i, r in d.rejected}
    assert "INV-10" in reasons["BBB"]
    assert "INV-01" in reasons["CCC"]


def test_inv02_cash_and_inv09_count():
    # 현금 100만으로 55만 매수 2건 — 두 번째는 INV-02 (개별로는 11% < 캡)
    intents = [caps.OrderIntent(f"S{i}", "BUY", amount_krw=550_000) for i in range(2)]
    d = _check(intents, cash=1_000_000.0)
    assert len(d.cleared) == 1 and "INV-02" in d.rejected[0][1]
    # 일일 횟수 상한 (INV-09 = 20)
    state = _state()
    state.daily_order_count = INV.orders.daily_max_count
    d2 = _check([caps.OrderIntent("AAA", "SELL", quantity=1, est_price_krw=1000)], state)
    assert not d2.cleared and "INV-09" in d2.rejected[0][1]


def test_cleared_intent_cannot_be_forged():
    """타입 테스트: 검사를 건너뛴 의도는 게이트에 들어갈 타입이 없다."""
    with pytest.raises(TypeError, match="caps.check"):
        caps.ClearedIntent(
            symbol="EVIL", side="BUY", quantity=1.0, amount_krw=None,
            est_price_krw=1.0, notional_krw=1.0, intent_hash="x",
        )


def test_inv07_circuit_breaker_trips_refuse_all():
    """DoD: INV-07 도달 시 전면 거부 모드."""
    state = _state(equity=5_000_000.0)
    tripped = state.update_equity(5_000_000.0 * (1 - INV.circuit_breaker.daily_loss_pct / 100), INV)
    assert tripped and state.cb_tripped
    d = _check([caps.OrderIntent("AAA", "SELL", quantity=1, est_price_krw=1000)], state)
    assert not d.cleared and "전면 거부" in d.rejected[0][1]


# ── DoD: preview 없는 execute 불가 (타입 + 런타임 네거티브) ─────────────


def _gate(registry, clock=None, live=False, client=None):
    paper = PaperPortfolio(cash=5_000_000.0)
    quotes = {"AAA": 70_000.0, "DDD": 50_000.0}
    return Gate(
        registry, COSTS, live_trading=live, paper=paper,
        quotes=lambda s: quotes[s], official_client=client,
        receipt_ttl_s=300.0, clock=clock or (lambda: 0.0),
    ), paper


def test_execute_rejects_everything_but_receipts(registry):
    gate, _ = _gate(registry)
    for bogus in ("confirm-token-123", {"symbol": "AAA"}, 42, None):
        with pytest.raises(ReceiptRequired):
            gate.execute(bogus)


def test_paper_receipt_cannot_be_forged(registry):
    with pytest.raises(TypeError, match="preview"):
        PaperReceipt(
            intent_hash="h", symbol="AAA", side="BUY", qty=1.0,
            exec_price=1.0, commission=0.0, tax=0.0, expires_at=1e9,
            receipt_hash="fake",
        )


def test_preview_requires_cleared_intent(registry):
    gate, _ = _gate(registry)
    with pytest.raises(ReceiptRequired):
        gate.preview({"symbol": "AAA", "side": "BUY"})  # dict는 타입이 없다


def test_tampered_receipt_is_rejected(registry):
    gate, _ = _gate(registry)
    [ci] = _check([caps.OrderIntent("AAA", "BUY", quantity=8, est_price_krw=70_000)]).cleared
    receipt = gate.preview(ci)
    forged = dataclasses.replace(receipt, qty=1_000.0)  # seal은 복제돼도 해시가 어긋난다
    with pytest.raises(GateError, match="변조"):
        gate.execute(forged)


def test_expired_receipt_is_rejected(registry):
    now = [0.0]
    gate, _ = _gate(registry, clock=lambda: now[0])
    [ci] = _check([caps.OrderIntent("AAA", "BUY", quantity=8, est_price_krw=70_000)]).cleared
    receipt = gate.preview(ci)
    now[0] = 301.0  # TTL 300s 경과
    with pytest.raises(GateError, match="만료"):
        gate.execute(receipt)


def test_paper_roundtrip_fills_and_records(registry):
    """preview → execute 정상 경로: 페이퍼 체결 + registry 주문 기록."""
    gate, paper = _gate(registry)
    [buy] = _check([caps.OrderIntent("AAA", "BUY", quantity=8, est_price_krw=70_000)]).cleared
    fill = gate.execute(gate.preview(buy))
    assert paper.qty["AAA"] == pytest.approx(8.0)
    assert paper.cash < 5_000_000.0 - 8 * 70_000  # 슬리피지+수수료가 불리한 방향
    [sell] = _check(
        [caps.OrderIntent("AAA", "SELL", quantity=8, est_price_krw=70_000)],
        positions={"AAA": 560_000.0},
    ).cleared
    gate.execute(gate.preview(sell))
    assert "AAA" not in paper.qty
    orders = registry.rows("orders")
    assert len(orders) == 2 and fill["side"] == "BUY"


def test_live_path_is_structurally_absent(registry, official_client):
    """live_trading=true + 공식 preview까지 가도 실물 분기는 NotImplementedError —
    Phase 7 전까지 실주문 경로는 저장소에 존재하지 않는다 (IMPL-07)."""
    gate, _ = _gate(registry, live=True, client=official_client)
    [ci] = _check([caps.OrderIntent("AAPL", "BUY", quantity=1, est_price_krw=290_000)]).cleared
    receipt = gate.preview(ci)
    assert isinstance(receipt, official_order.PreviewReceipt)
    with pytest.raises(NotImplementedError, match="Phase 7"):
        gate.execute(receipt)


def test_official_receipt_refused_when_paper_mode(registry, official_client):
    gate_live, _ = _gate(registry, live=True, client=official_client)
    [ci] = _check([caps.OrderIntent("AAPL", "BUY", quantity=1, est_price_krw=290_000)]).cleared
    live_receipt = gate_live.preview(ci)
    gate_paper, _ = _gate(registry)
    with pytest.raises(GateError, match="live_trading=false"):
        gate_paper.execute(live_receipt)


# ── DoD: 회전율 51% → 에스컬레이션 + 단계 계획 (GATE-03 4단) ────────────


def _seed_gates_passed(registry, sid: str):
    registry.append_strategy_transition(sid, "backtest", "paper", "LC-G2 테스트 시드")
    registry.append_event(EVENT_PAPER_PASSED, "audit", {"strategy_id": sid})


def test_turnover_51_escalates_with_staged_plan(registry):
    strategy = parse_strategy(_valid_dict())
    _seed_gates_passed(registry, strategy.meta.id)
    current = {"AAA": 0.51, "CASH_": 0.0}
    target = {"BBB": 0.51}  # 회전율 = (0.51+0.51)/2 = 0.51 > 0.50
    result = approve_switch(
        strategy, registry, INV,
        universe_symbols={"us_core": ["AAA", "BBB"]},
        whitelist={"us_core": {"AAA", "BBB"}},
        stock_master={"AAA": ("STOCK", None), "BBB": ("STOCK", None)},
        current_weights=current, target_weights=target,
    )
    assert not result.auto_approved
    assert any("INV-06" in r for r in result.reasons)
    assert result.staged_plan is not None and len(result.staged_plan) >= 2
    # 각 단계의 회전율이 자동 승인 상한 이하
    from quantbot.engine.portfolio import turnover
    prev = current
    for stage in result.staged_plan:
        assert turnover(prev, stage) <= INV.turnover.auto_approve_max_pct / 100 + 1e-9
        prev = stage
    assert prev == result.staged_plan[-1]


def test_auto_approval_full_pass(registry):
    strategy = parse_strategy(_valid_dict())
    _seed_gates_passed(registry, strategy.meta.id)
    result = approve_switch(
        strategy, registry, INV,
        universe_symbols={"us_core": ["AAA"]},
        whitelist={"us_core": {"AAA"}},
        stock_master={"AAA": ("STOCK", None)},
        current_weights={"AAA": 0.10}, target_weights={"AAA": 0.12},
    )
    assert result.auto_approved and result.staged_plan is None


def test_missing_paper_record_blocks_auto_approval(registry):
    """INV-08 — 페이퍼 미통과 자동 승인 금지."""
    strategy = parse_strategy(_valid_dict())
    registry.append_strategy_transition(strategy.meta.id, "backtest", "paper", "시드")
    result = approve_switch(
        strategy, registry, INV,
        universe_symbols={"us_core": ["AAA"]},
        whitelist={"us_core": {"AAA"}},
        stock_master={"AAA": ("STOCK", None)},
        current_weights={}, target_weights={"AAA": 0.1},
    )
    assert not result.auto_approved
    assert any("INV-08" in r for r in result.reasons)


# ── 결정 2 — 주문 단위별 백테스트 집행 ──────────────────────────────────


def test_whole_unit_holds_integer_shares(uptrend_store):
    from quantbot.backtest.sim import simulate
    from conftest import LOW_COSTS, SMALL_METH, buy_and_hold_first

    frac = simulate(uptrend_store, 0, 40, buy_and_hold_first, {"weight": 0.5},
                    LOW_COSTS, SMALL_METH, order_unit="fractional")
    whole = simulate(uptrend_store, 0, 40, buy_and_hold_first, {"weight": 0.5},
                     LOW_COSTS, SMALL_METH, order_unit="whole")
    # whole 단위: 모든 주문 수량이 정수 (notional / exec_price 가 정수)
    assert whole.equity[-1] != frac.equity[-1]  # 절사 왜곡이 실제로 반영된다
    with pytest.raises(Exception):
        simulate(uptrend_store, 0, 10, buy_and_hold_first, {}, LOW_COSTS,
                 SMALL_METH, order_unit="half")