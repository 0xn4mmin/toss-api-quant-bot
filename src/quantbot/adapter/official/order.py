"""GATE 전용 주문 표면 (IMPL-02 장치 1, §I3 v1.1) — 타입이 곧 안전 규칙이다.

- PreviewReceipt는 preview()만 만든다 (생성자 가드 — 모듈 내부 봉인).
- execute(receipt: PreviewReceipt)는 문자열 token을 받는 오버로드가 없다.
  "preview 없는 execute"는 타입 체커와 런타임 양쪽에서 표현이 안 되는 프로그램이다.
- 공식 API에 preview 엔드포인트가 없으므로(OAS v1.1.5) preview는 조회로 합성한다:
  현재가(prices) + 실측 수수료(commissions) + 매수가능금액/판매가능수량.
- 실물 집행 분기는 Phase 7까지 저장소에 존재하지 않는다 (IMPL-07) —
  execute()는 검증 후 NotImplementedError. Phase 7이 POST /api/v1/orders
  (clientOrderId 멱등키, 재시도 없음)를 이 자리에 넣는다.

이 모듈은 engine.gate만 import할 수 있다 (아키텍처 테스트가 강제).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from quantbot._canon import canonical_json, sha256_hex
from quantbot.adapter.official import md, tradeinfo
from quantbot.adapter.official.http import OpenApiClient


class OrderSurfaceError(Exception):
    pass


class ReceiptExpired(OrderSurfaceError):
    pass


class ReceiptTampered(OrderSurfaceError):
    pass


class LiveExecutionNotBuilt(NotImplementedError):
    """실물 분기 부재 — Phase 7의 머지 조건이 충족될 때까지 존재하지 않는다."""


_SEAL = object()  # preview()만 이 참조를 넘길 수 있다


def _receipt_hash(intent_hash: str, symbol: str, side: str,
                  quantity: float | None, amount: float | None,
                  est_price: float) -> str:
    return sha256_hex(canonical_json({
        "intent_hash": intent_hash, "symbol": symbol, "side": side,
        "quantity": quantity, "amount": amount, "est_price": est_price,
    }))


@dataclass(frozen=True)
class PreviewReceipt:
    """preview()의 반환값으로만 존재하는 타입 — 게이트 execute의 유일한 입장권."""

    intent_hash: str            # caps 통과 의도의 해시 — execute 시 재대조
    symbol: str
    side: str
    quantity: float | None
    amount: float | None
    est_price: float
    est_commission: float
    est_notional: float
    expires_at: float           # monotonic 기준 만료 (TTL)
    receipt_hash: str           # 필드 변조 검출 (dataclasses.replace 위조 차단)
    _seal: object = field(repr=False, default=None)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise TypeError(
                "PreviewReceipt는 preview()만 생성할 수 있다 (IMPL-02 장치 1)"
            )


def preview(
    client: OpenApiClient,
    *,
    intent_hash: str,
    symbol: str,
    side: str,
    quantity: float | None = None,
    amount: float | None = None,
    ttl_s: float,
    clock: Callable[[], float] = time.monotonic,
) -> PreviewReceipt:
    """조회 합성 preview — 예상 체결가·수수료·가용성 검증을 거쳐 영수증을 봉인한다."""
    if (quantity is None) == (amount is None):
        raise OrderSurfaceError("quantity와 amount 중 정확히 하나가 필요하다")
    quote = md.prices(client, [symbol])[0]
    price = float(quote.lastPrice)
    notional = amount if amount is not None else float(quantity) * price
    comms = tradeinfo.commissions(client)
    market = "KR" if symbol.isdigit() else "US"
    rate = next(
        (float(c.commissionRate) for c in comms if c.marketCountry == market), 0.0
    )
    est_commission = notional * rate
    if side == "BUY":
        bp = tradeinfo.buying_power(client, quote.currency)
        if notional + est_commission > float(bp.cashBuyingPower):
            raise OrderSurfaceError(
                f"매수 가능 금액 부족: {bp.cashBuyingPower} < {notional + est_commission:.2f}"
            )
    elif side == "SELL" and quantity is not None:
        sq = tradeinfo.sellable_quantity(client, symbol)
        if float(quantity) > float(sq.sellableQuantity):
            raise OrderSurfaceError(
                f"판매 가능 수량 부족: {sq.sellableQuantity} < {quantity}"
            )
    return PreviewReceipt(
        intent_hash=intent_hash, symbol=symbol, side=side,
        quantity=quantity, amount=amount, est_price=price,
        est_commission=est_commission, est_notional=notional,
        expires_at=clock() + ttl_s,
        receipt_hash=_receipt_hash(intent_hash, symbol, side, quantity, amount, price),
        _seal=_SEAL,
    )


def execute(
    client: OpenApiClient,
    receipt: PreviewReceipt,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """실물 집행 — Phase 7 전까지 분기 자체가 없다. 검증만 수행하고 거부한다."""
    if not isinstance(receipt, PreviewReceipt):
        raise TypeError(
            f"execute는 PreviewReceipt만 받는다 — {type(receipt).__name__} 불가 "
            "(str token 오버로드는 존재하지 않는다, §I3)"
        )
    if receipt.receipt_hash != _receipt_hash(
        receipt.intent_hash, receipt.symbol, receipt.side,
        receipt.quantity, receipt.amount, receipt.est_price,
    ):
        raise ReceiptTampered("영수증 필드가 preview 시점과 다르다")
    if clock() > receipt.expires_at:
        raise ReceiptExpired("preview 영수증이 만료됐다 — preview부터 다시")
    raise LiveExecutionNotBuilt(
        "실물 주문 분기는 Phase 7(페이퍼 1개월 게이트 통과 후)에서 구현된다 — "
        "그 전까지 이 경로는 저장소에 존재하지 않는다 (IMPL-07)"
    )
