"""tossctl 명령별 입출력 계약 (IMPL-03) — 응답의 문법, fail-closed.

모든 모델은 strict + extra="forbid": 알 수 없는 필드·누락 필드·타입 불일치는
부분 파싱 없이 전체 거부된다 (Phase 0 invariants 로더와 같은 방식). 검증 실패는
예외가 아니라 신호다 — SchemaDriftError로 분류되어 엔진에 상향 보고되고,
엔진(Phase 5 watcher)은 이를 fail-safe hold 트리거로 취급한다. 비공식 API의
스키마 변경이 봇 전체에서 흡수되는 지점이 정확히 이 한 곳이다.

주의(§I8): 아래 필드 구성은 실측 전 초안이다. 실제 tossctl 응답과 다르면
그것은 버그가 아니라 계약을 실측으로 확정하는 Phase 2 후속 작업이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

from quantbot.adapter.proc import TossctlRunner


@dataclass(frozen=True)
class SchemaDrift:
    """상향 보고용 신호 payload — 엔진이 registry 이벤트·hold 트리거로 소비한다."""

    command: tuple[str, ...]
    model: str
    detail: str


class SchemaDriftError(Exception):
    """tossctl 응답이 계약을 벗어났다 — 부분 수용 없음, 전체 거부."""

    def __init__(self, drift: SchemaDrift) -> None:
        super().__init__(
            f"SchemaDrift: {' '.join(drift.command)} → {drift.model}: {drift.detail}"
        )
        self.drift = drift


class Contract(BaseModel):
    """전 계약의 공통 설정 — strict(타입 강제) + extra 거부(미지 필드 거부)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def call(runner: TossctlRunner, args: list[str], model: type[Contract]) -> Contract:
    """proc 실행 → JSON 파싱 → 계약 검증의 3단 — 어댑터 함수의 유일한 통과 경로."""
    data = runner.run_json(args)
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise SchemaDriftError(
            SchemaDrift(command=tuple(args), model=model.__name__, detail=str(e))
        ) from e


# ═══ health.* — doctor · auth status ═══════════════════════════════════


class DoctorCheck(Contract):
    name: str
    status: str
    detail: str | None = None


class DoctorReport(Contract):
    ok: bool
    checks: list[DoctorCheck]


class AuthStatus(Contract):
    authenticated: bool
    expires_at: str | None = None


# ═══ md.* — quote get/batch/chart/flows/orderbook/warnings/commission/limits ═══


class Quote(Contract):
    symbol: str
    price: float
    currency: str
    as_of: str


class QuoteBatch(Contract):
    quotes: list[Quote]


class ChartBar(Contract):
    date: str
    close: float
    volume: float


class Chart(Contract):
    symbol: str
    period: str
    bars: list[ChartBar]


class FlowRow(Contract):
    date: str
    foreign_net: float
    inst_net: float
    traded_value: float


class Flows(Contract):
    symbol: str
    rows: list[FlowRow]


class OrderbookLevel(Contract):
    price: float
    qty: float


class Orderbook(Contract):
    symbol: str
    bids: list[OrderbookLevel]
    asks: list[OrderbookLevel]


class WarningFlags(Contract):
    symbol: str
    flags: list[str]  # 빈 리스트 = clean


class Commission(Contract):
    rate: float
    min_fee: float
    currency: str


class TradeLimits(Contract):
    symbol: str
    unit: float           # 호가/수량 최소 단위 (RISK-05)
    fractional: bool      # 소수점 주문 가능 여부


# ═══ mkt.* — index / fx / hours / screener ══════════════════════════════


class IndexQuote(Contract):
    name: str
    value: float
    as_of: str


class FxRate(Contract):
    pair: str
    rate: float
    as_of: str


class MarketHours(Contract):
    market: str
    is_open: bool
    open_at: str | None = None
    close_at: str | None = None


class ScreenerRow(Contract):
    symbol: str
    name: str


class ScreenerResult(Contract):
    preset: str
    rows: list[ScreenerRow]


# ═══ acct.* — summary / positions ═══════════════════════════════════════


class AccountSummary(Contract):
    cash: float
    total_value: float
    currency: str


class Position(Contract):
    symbol: str
    qty: float
    avg_price: float
    currency: str


class Positions(Contract):
    positions: list[Position]


# ═══ ledger.* — orders list · transactions list (조회 전용 — 주문 발행 아님) ═══


class LedgerOrder(Contract):
    id: str
    symbol: str
    side: str
    qty: float
    status: str
    created_at: str


class LedgerOrders(Contract):
    items: list[LedgerOrder]


class Transaction(Contract):
    id: str
    symbol: str
    side: str
    qty: float
    price: float
    executed_at: str


class Transactions(Contract):
    items: list[Transaction]
