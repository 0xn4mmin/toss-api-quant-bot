"""Broker 프로토콜 동형성 — 공식 실물 조회와 모의 조회가 같은 표면 (§I3 v1.1)."""

from __future__ import annotations

import pytest

from quantbot.adapter.broker import Broker, OfficialBroker, PaperBroker
from quantbot.adapter.official.contracts import HoldingsItem, PriceResponse
from conftest import OFFICIAL_FIXTURES


def _paper() -> PaperBroker:
    item = HoldingsItem.model_validate(OFFICIAL_FIXTURES["/api/v1/holdings"]["items"][0])
    quote = PriceResponse.model_validate(OFFICIAL_FIXTURES["/api/v1/prices"][0])
    return PaperBroker(
        cash_by_currency={"KRW": 5_000_000.0},
        positions=[item],
        quotes={"AAPL": quote},
    )


def test_both_brokers_satisfy_protocol(official_client):
    assert isinstance(OfficialBroker(official_client), Broker)
    assert isinstance(_paper(), Broker)


def test_brokers_return_identical_shapes(official_client):
    """같은 질의에 같은 타입 — 페이퍼가 검증하는 것이 곧 live에서 돌 코드다."""
    real, paper = OfficialBroker(official_client), _paper()
    assert real.cash("KRW") == paper.cash("KRW") == pytest.approx(5_000_000.0)
    for b in (real, paper):
        pos = b.positions()
        assert isinstance(pos[0], HoldingsItem) and pos[0].symbol == "AAPL"
        q = b.quote("AAPL")
        assert isinstance(q, PriceResponse) and q.lastPrice == "213.55"


def test_broker_protocol_has_no_order_surface():
    """조회 표면에 주문 동사가 존재하지 않는다 — 구조 검사."""
    surface = [m for m in dir(Broker) if not m.startswith("_")]
    assert surface == sorted(["cash", "positions", "quote"])
    forbidden = ("place", "execute", "preview", "cancel", "amend", "buy", "sell")
    for b in (OfficialBroker, PaperBroker):
        exposed = {m for m in dir(b) if not m.startswith("_")}
        assert not any(any(f in m for f in forbidden) for m in exposed), exposed
