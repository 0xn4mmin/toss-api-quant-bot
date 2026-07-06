"""BT-D2 수정주가 판정 리포트 + Broker 프로토콜 동형성 검사."""

from __future__ import annotations

import pytest

from quantbot.adapter import md
from quantbot.adapter.broker import Broker, PaperBroker, TossctlBroker
from quantbot.adapter.contracts import Chart, ChartBar, Position, Quote

TOL = 0.05  # 판정 허용 오차 — 테스트 픽스처 값 (통상 일변동 상한 가정)


def _chart(closes: list[float], symbol: str = "TST") -> Chart:
    bars = [
        ChartBar(date=f"2026-06-{i + 1:02d}", close=c, volume=1000.0)
        for i, c in enumerate(closes)
    ]
    return Chart(symbol=symbol, period="day", bars=bars)


def test_adjusted_series_verdict():
    """DoD(BT-D2): 조정 종가 — 분할일에 연속적 → 'adjusted' 리포트."""
    # 4:1 분할이 2026-06-03에 있었지만 종가는 연속(이미 조정됨)
    chart = _chart([100.0, 101.0, 100.5, 102.0])
    report = md.adjusted_price_report(chart, "2026-06-03", 4.0, TOL)
    assert report["verdict"] == "adjusted"
    assert report["observed_move"] == pytest.approx(100.5 / 101.0)


def test_unadjusted_series_verdict():
    """DoD(BT-D2): 미조정 종가 — 분할일에 1/ratio 점프 → 'unadjusted' 리포트."""
    chart = _chart([100.0, 101.0, 25.2, 25.5])  # 4:1 분할이 그대로 보인다
    report = md.adjusted_price_report(chart, "2026-06-03", 4.0, TOL)
    assert report["verdict"] == "unadjusted"
    assert report["unadjusted_expected_move"] == pytest.approx(0.25)


def test_inconclusive_series_verdict():
    """중간 지대는 'inconclusive' — 미해결이면 백테스트 무효 (BT-D2)."""
    chart = _chart([100.0, 101.0, 60.0, 61.0])  # 분할로도 정상 변동으로도 설명 불가
    report = md.adjusted_price_report(chart, "2026-06-03", 4.0, TOL)
    assert report["verdict"] == "inconclusive"


def test_report_requires_split_context():
    chart = _chart([100.0, 101.0])
    with pytest.raises(ValueError):
        md.adjusted_price_report(chart, "2099-01-01", 4.0, TOL)
    with pytest.raises(ValueError):
        md.adjusted_price_report(chart, "2026-06-01", 4.0, TOL)  # 이전 봉 없음
    with pytest.raises(ValueError):
        md.adjusted_price_report(chart, "2026-06-02", 1.0, TOL)  # 비율 ≤ 1


# ── Broker 프로토콜 — 실물/모의가 같은 표면 (§I3) ────────────────────────


def _paper() -> PaperBroker:
    return PaperBroker(
        cash=5_000_000.0,
        positions=[Position(symbol="AAPL", qty=1.5, avg_price=205.0, currency="USD")],
        quotes={"AAPL": Quote(symbol="AAPL", price=213.55, currency="USD",
                              as_of="2026-07-06T05:00:00+09:00")},
    )


def test_both_brokers_satisfy_protocol(tossctl_runner):
    assert isinstance(TossctlBroker(tossctl_runner), Broker)
    assert isinstance(_paper(), Broker)


def test_brokers_return_identical_shapes(tossctl_runner):
    """같은 질의에 같은 타입 — 페이퍼가 검증하는 것이 곧 live에서 돌 코드다."""
    real, paper = TossctlBroker(tossctl_runner), _paper()
    assert real.cash() == paper.cash() == pytest.approx(5_000_000.0)
    for b in (real, paper):
        pos = b.positions()
        assert pos[0].symbol == "AAPL" and isinstance(pos[0], Position)
        q = b.quote("AAPL")
        assert isinstance(q, Quote) and q.price == pytest.approx(213.55)


def test_broker_protocol_has_no_order_surface():
    """조회 Phase의 브로커 표면에 주문 동사가 존재하지 않는다 — 구조 검사."""
    surface = [m for m in dir(Broker) if not m.startswith("_")]
    assert surface == sorted(["cash", "positions", "quote"])
    forbidden = ("place", "execute", "preview", "cancel", "amend", "buy", "sell")
    for b in (TossctlBroker, PaperBroker):
        exposed = {m for m in dir(b) if not m.startswith("_")}
        assert not any(any(f in m for f in forbidden) for m in exposed), exposed