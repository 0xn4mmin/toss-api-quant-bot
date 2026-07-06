"""Phase 5 DoD — 재생 스트림 손절 트리거 / 하트비트 소실→hold / SchemaDrift→hold /
hold 중 신규 주문 caps 거부 / 유령 체결 픽스처로 기동 시 hold."""

from __future__ import annotations

import json

import pytest

from quantbot.adapter.contracts import SchemaDrift
from quantbot.adapter.official.contracts import OrderRecord
from quantbot.adapter.tossctl import stream
from quantbot.adapter.tossctl.proc import TossctlRunner
from quantbot.engine import caps
from quantbot.engine.invariants import load_invariants
from quantbot.engine.reconcile import reconcile_startup
from quantbot.engine.watcher import (
    EVENT_HOLD,
    EVENT_STOP_LOSS,
    StopLossBreach,
    Watcher,
    WatcherConfig,
)
from conftest import OFFICIAL_FIXTURES, make_run_policy

INV = load_invariants()
CFG = WatcherConfig(heartbeat_timeout_s=90.0, max_consecutive_errors=3)


def _ev(**kw) -> stream.PushEvent:
    base = {"type": "heartbeat", "ts": "2026-07-06T10:00:00+09:00"}
    base.update(kw)
    return stream.PushEvent.model_validate(base)


def _watcher(registry, clock=None, positions=None):
    state = caps.CapsState()
    state.start_day(5_000_000.0)
    escalations = []
    w = Watcher(
        registry=registry, caps_state=state, config=CFG,
        positions=lambda: positions or {"AAA": (70_000.0, 0.10)},
        escalate=lambda reason, ctx: escalations.append(reason),
        clock=clock or (lambda: 0.0),
    )
    return w, state, escalations


def test_stop_loss_triggers_from_replayed_stream(registry):
    """DoD: 재생 스트림으로 손절 트리거 — entry×(1−stop) 이하에서 발화."""
    w, state, _ = _watcher(registry)
    events = [
        _ev(type="quote", symbol="AAA", price=64_000.0),   # -8.6% — 아직
        _ev(type="quote", symbol="AAA", price=62_999.0),   # -10.001% — 발화
        _ev(type="quote", symbol="BBB", price=1.0),        # 미보유 — 무시
    ]
    breaches = w.process(events)
    assert breaches == [StopLossBreach("AAA", 62_999.0, 70_000.0, 0.10)]
    assert len(registry.events(EVENT_STOP_LOSS)) == 1
    assert not state.hold


def test_heartbeat_loss_triggers_hold(registry):
    """DoD: 하트비트 소실 → hold."""
    now = [0.0]
    w, state, escalations = _watcher(registry, clock=lambda: now[0])
    w.process([_ev()])          # t=0 정상 이벤트
    now[0] = 91.0               # 침묵 91초 > 90초
    w.check_heartbeat()
    assert state.hold
    holds = registry.events(EVENT_HOLD)
    assert len(holds) == 1 and holds[0]["payload"]["reason"] == "heartbeat_lost"
    assert escalations == ["heartbeat_lost"]


def test_schema_drift_triggers_hold(registry):
    """DoD: SchemaDrift → hold — 비공식 API 스키마 변경의 흡수 지점."""
    w, state, _ = _watcher(registry)
    drift = SchemaDrift(source="tossctl", command=("push", "listen"),
                        model="PushEvent", detail="unknown field")
    w.process([drift])
    assert state.hold
    assert registry.events(EVENT_HOLD)[0]["payload"]["reason"] == "schema_drift"


def test_consecutive_errors_trigger_hold(registry):
    w, state, _ = _watcher(registry)
    w.process([_ev(type="error", detail="boom")] * 2)
    assert not state.hold                       # 임계(3) 미만
    w.process([_ev(), _ev(type="error")] * 3)   # 성공이 카운터를 리셋
    assert not state.hold
    w.process([_ev(type="error")] * 3)
    assert state.hold


def test_hold_blocks_new_orders_and_auto_stop_loss(registry):
    """DoD: hold 중 신규 주문이 caps에서 거부 + 자동 손절도 중단 (§9 전면 동결)."""
    w, state, _ = _watcher(registry)
    w.hold("test_reason")
    d = caps.check(
        [caps.OrderIntent("AAA", "SELL", quantity=1, est_price_krw=1000)],
        INV, state, equity_krw=5_000_000.0, cash_krw=5_000_000.0,
        position_value_krw={},
    )
    assert not d.cleared and "전면 거부" in d.rejected[0][1]
    breaches = w.process([_ev(type="quote", symbol="AAA", price=1.0)])  # 대폭락
    assert breaches == []  # 오염 가능 데이터 위의 손절 오발동 방지


def test_hold_release_requires_tier2(registry):
    w, state, _ = _watcher(registry)
    w.hold("x")
    with pytest.raises(PermissionError):
        w.release_hold(confirmed_by_tier2=False, detail="셀프 해제 시도")
    w.release_hold(confirmed_by_tier2=True, detail="/resume confirm")
    assert not state.hold


def test_ghost_fill_fixture_starts_held(registry):
    """DoD: 가짜 유령 체결 픽스처로 기동 시 hold 진입 (RISK-06)."""
    w, state, _ = _watcher(registry)
    ghost = OrderRecord.model_validate(OFFICIAL_FIXTURES["/api/v1/orders/ord-1"])
    result = reconcile_startup(registry, [ghost], w)
    assert not result.clean and result.ghosts == ("ord-1",)
    assert state.hold
    assert registry.events(EVENT_HOLD)[0]["payload"]["reason"] == "ghost_orders"


def test_reconcile_clean_when_registry_knows_orders(registry):
    w, state, _ = _watcher(registry)
    known = OrderRecord.model_validate(OFFICIAL_FIXTURES["/api/v1/orders/ord-1"])
    registry.append_order("hash-1", "live_filled", {"orderId": "ord-1"})
    result = reconcile_startup(registry, [known], w)
    assert result.clean and not state.hold


# ── 스트림: 진짜 subprocess JSONL + 계약 이탈은 드리프트 신호 ────────────


def test_push_stream_yields_typed_events_and_drift(tmp_path, monkeypatch):
    fx = tmp_path / "fx"
    fx.mkdir()
    lines = [
        {"type": "heartbeat", "ts": "t1"},
        {"type": "quote", "ts": "t2", "symbol": "AAA", "price": 100.0},
        {"type": "fill", "ts": "t3", "symbol": "AAA", "qty": 1.0, "side": "BUY",
         "surprise": True},                       # 계약 이탈 → 드리프트
    ]
    (fx / "push_listen.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines), encoding="utf-8"
    )
    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(fx))
    runner = TossctlRunner(make_run_policy())
    out = list(stream.events(runner))
    assert isinstance(out[0], stream.PushEvent) and out[0].type == "heartbeat"
    assert out[1].price == 100.0
    assert isinstance(out[2], SchemaDrift) and out[2].model == "PushEvent"