"""주문 의도 검사 (IMPL-04, GATE-01) — INV-01/02/09/10 + INV-07 일일 CB.

타입이 곧 안전 규칙이다 (IMPL-02 장치 1): 검사를 통과한 의도만 ClearedIntent로
봉인되고, ClearedIntent는 이 모듈의 check()만 만들 수 있다(생성자 가드).
불변식 검사를 건너뛴 주문 의도는 게이트에 들어갈 타입이 없다.

INV-07 도달 또는 fail-safe hold 시 caps는 전면 거부 모드다 — 예외도 완화도 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

from quantbot._canon import canonical_json, sha256_hex
from quantbot.engine.invariants import Invariants

PCT = 100.0  # 백분율 ↔ 비율 (단위 변환 상수)


class CapsError(Exception):
    pass


@dataclass(frozen=True)
class OrderIntent:
    """검사 전 주문 의도 — 누구나 만들 수 있는 평범한 값."""

    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float | None = None      # 수량 기반 (whole/소수점 매도)
    amount_krw: float | None = None    # 금액 기반 (fractional 매수, KRW 환산)
    est_price_krw: float = 0.0         # 검사 시점 추정가 (KRW 환산)

    def notional_krw(self) -> float:
        if self.amount_krw is not None:
            return self.amount_krw
        if self.quantity is None or self.est_price_krw <= 0:
            raise CapsError(f"{self.symbol}: 수량·금액 중 하나와 추정가가 필요하다")
        return self.quantity * self.est_price_krw


_SEAL = object()  # 모듈 내부 전용 — check()만 이 참조를 넘길 수 있다


@dataclass(frozen=True)
class ClearedIntent:
    """caps.check()를 통과한 주문 의도 — 게이트에 들어갈 수 있는 유일한 타입."""

    symbol: str
    side: str
    quantity: float | None
    amount_krw: float | None
    est_price_krw: float
    notional_krw: float
    intent_hash: str
    _seal: object = field(repr=False, default=None)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise TypeError(
                "ClearedIntent는 caps.check()만 생성할 수 있다 — "
                "불변식 검사를 건너뛴 의도는 게이트에 들어갈 타입이 없다 (IMPL-02 장치 1)"
            )


def intent_hash(intent: OrderIntent) -> str:
    return sha256_hex(canonical_json({
        "symbol": intent.symbol, "side": intent.side,
        "quantity": intent.quantity, "amount_krw": intent.amount_krw,
        "est_price_krw": intent.est_price_krw,
    }))


@dataclass
class CapsState:
    """일일 카운터·서킷브레이커·hold — 엔진이 하루 단위로 관리한다."""

    daily_order_count: int = 0
    day_start_equity: float | None = None
    cb_tripped: bool = False           # INV-07 — 도달 시 전면 거부
    hold: bool = False                 # fail-safe hold (GATE-05) — 전면 거부

    def start_day(self, equity: float) -> None:
        self.day_start_equity = equity
        self.daily_order_count = 0
        self.cb_tripped = False

    def update_equity(self, equity: float, inv: Invariants) -> bool:
        """일일 손실 추적 (INV-07) — 임계 도달 시 CB. 반환값 = 이번에 발동했는가."""
        if self.day_start_equity is None or self.day_start_equity <= 0:
            return False
        loss_pct = (self.day_start_equity - equity) / self.day_start_equity * PCT
        if not self.cb_tripped and loss_pct >= inv.circuit_breaker.daily_loss_pct:
            self.cb_tripped = True
            return True
        return False

    @property
    def refuse_all(self) -> bool:
        return self.cb_tripped or self.hold


@dataclass(frozen=True)
class CapsDecision:
    cleared: tuple[ClearedIntent, ...]
    rejected: tuple[tuple[OrderIntent, str], ...]  # (의도, 사유)

    @property
    def all_cleared(self) -> bool:
        return not self.rejected


def check(
    intents: list[OrderIntent],
    inv: Invariants,
    state: CapsState,
    *,
    equity_krw: float,
    cash_krw: float,
    position_value_krw: Mapping[str, float],
) -> CapsDecision:
    """주문 의도 목록을 검사해 통과분만 ClearedIntent로 봉인한다 (GATE-01).

    거부는 완화 없는 거부다 — 통과분과 거부분이 함께 반환되고, 거부 사유는
    게이트가 registry·에스컬레이션으로 올린다.
    """
    cleared: list[ClearedIntent] = []
    rejected: list[tuple[OrderIntent, str]] = []
    if equity_krw <= 0:
        raise CapsError(f"계좌 평가액이 비정상이다: {equity_krw}")

    cap = inv.position.max_weight_pct / PCT
    budget_cash = cash_krw
    count = state.daily_order_count
    # 같은 배치에서 이미 통과한 매수 명목가 — 종목 쪼개기로 캡을 우회 못 하게
    pending_buy_krw: dict[str, float] = {}

    for intent in intents:
        if state.refuse_all:
            rejected.append((intent, "INV-07/hold: 전면 거부 모드"))
            continue
        try:
            notional = intent.notional_krw()
        except CapsError as e:
            rejected.append((intent, str(e)))
            continue
        if notional <= 0:
            rejected.append((intent, "명목 금액 ≤ 0"))
            continue
        if notional > inv.orders.per_order_max_amount_krw:
            rejected.append((
                intent,
                f"INV-10: 1회 금액 {notional:.0f} > 상한 "
                f"{inv.orders.per_order_max_amount_krw}",
            ))
            continue
        if count >= inv.orders.daily_max_count:
            rejected.append((intent, f"INV-09: 일일 주문 {count}회 상한 도달"))
            continue
        if intent.side == "BUY":
            if notional > budget_cash:
                rejected.append((
                    intent, f"INV-02: 현금 {budget_cash:.0f} < 매수 {notional:.0f}",
                ))
                continue
            weight_after = (
                position_value_krw.get(intent.symbol, 0.0)
                + pending_buy_krw.get(intent.symbol, 0.0)
                + notional
            ) / equity_krw
            if weight_after > cap + 1e-12:
                rejected.append((
                    intent,
                    f"INV-01: {intent.symbol} 매수 후 비중 {weight_after:.4f} > "
                    f"캡 {cap:.4f} — 정상 경로라면 전략 계층 버그 (§S5)",
                ))
                continue
            budget_cash -= notional
            pending_buy_krw[intent.symbol] = (
                pending_buy_krw.get(intent.symbol, 0.0) + notional
            )
        count += 1
        cleared.append(ClearedIntent(
            symbol=intent.symbol, side=intent.side, quantity=intent.quantity,
            amount_krw=intent.amount_krw, est_price_krw=intent.est_price_krw,
            notional_krw=notional, intent_hash=intent_hash(intent), _seal=_SEAL,
        ))
    return CapsDecision(cleared=tuple(cleared), rejected=tuple(rejected))
