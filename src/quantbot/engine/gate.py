"""단일 안전 게이트 (IMPL-04, ARCH-03) — ClearedIntent → preview → execute 직렬 집행.

이 모듈만 adapter.official.order를 import할 수 있다 (아키텍처 테스트 강제).
게이트의 타입 규칙:
- submit은 ClearedIntent만 받는다 — caps.check()를 거치지 않은 의도는 타입이 없다.
- 집행은 preview 영수증(PaperReceipt/PreviewReceipt)만 받는다 — 영수증은 각
  preview 함수만 만들 수 있고, 필드 변조는 해시 재대조가 잡는다.
- runtime live_trading=false 면 무조건 페이퍼 경로 (§I3). live 경로의 실물
  분기는 Phase 7까지 존재하지 않는다 (adapter.official.order가 거부).

페이퍼 체결 모델은 백테스트 sim과 같은 산식(슬리피지 불리한 방향 + 수수료
하한 + 매도세)이다 — 페이퍼가 검증하는 것이 곧 live에서 돌 코드이게 (§I3).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

from quantbot._canon import canonical_json, sha256_hex
from quantbot.adapter.fills import CostModel
from quantbot.adapter.official import order as official_order
from quantbot.adapter.official.http import OpenApiClient
from quantbot.engine.caps import ClearedIntent
from quantbot.engine.registry import Registry

EVENT_PREVIEW = "order_preview"
ORDER_STATUS_PAPER_FILLED = "paper_filled"


class GateError(Exception):
    pass


class ReceiptRequired(TypeError):
    pass


_PAPER_SEAL = object()


def _paper_hash(intent_hash: str, symbol: str, side: str, qty: float,
                exec_price: float) -> str:
    return sha256_hex(canonical_json({
        "intent_hash": intent_hash, "symbol": symbol, "side": side,
        "qty": qty, "exec_price": exec_price,
    }))


@dataclass(frozen=True)
class PaperReceipt:
    """페이퍼 preview의 반환값으로만 존재하는 타입 — 게이트 집행의 입장권."""

    intent_hash: str
    symbol: str
    side: str
    qty: float
    exec_price: float           # 슬리피지 반영가
    commission: float
    tax: float
    expires_at: float
    receipt_hash: str
    _seal: object = field(repr=False, default=None)

    def __post_init__(self) -> None:
        if self._seal is not _PAPER_SEAL:
            raise TypeError("PaperReceipt는 Gate.preview()만 생성할 수 있다")


@dataclass
class PaperPortfolio:
    """페이퍼 계좌 상태 — 체결 결과가 여기 쌓인다 (상태는 registry로 복원 가능)."""

    cash: float
    qty: dict[str, float] = field(default_factory=dict)
    avg_cost: dict[str, float] = field(default_factory=dict)

    def equity(self, prices: Mapping[str, float]) -> float:
        return self.cash + sum(
            q * prices[s] for s, q in sorted(self.qty.items())
        )

    def position_values(self, prices: Mapping[str, float]) -> dict[str, float]:
        return {s: q * prices[s] for s, q in sorted(self.qty.items())}


class Gate:
    """모든 실주문이 통과하는 유일한 띠 (ARCH-03)."""

    def __init__(
        self,
        registry: Registry,
        cost_model: CostModel,
        *,
        live_trading: bool,
        paper: PaperPortfolio,
        quotes: Callable[[str], float],
        official_client: OpenApiClient | None = None,
        receipt_ttl_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if live_trading and official_client is None:
            raise GateError("live_trading에는 공식 클라이언트가 필요하다")
        self._registry = registry
        self.costs = cost_model  # 사이징(수수료 여유 계산)이 같은 모델을 쓴다
        self._live = live_trading
        self._paper = paper
        self._quotes = quotes
        self._client = official_client
        self._ttl = receipt_ttl_s
        self._clock = clock

    # ── preview — ClearedIntent만 입장 ─────────────────────────────────

    def preview(self, ci: ClearedIntent) -> PaperReceipt | official_order.PreviewReceipt:
        if not isinstance(ci, ClearedIntent):
            raise ReceiptRequired(
                f"preview는 ClearedIntent만 받는다 — {type(ci).__name__} 불가 "
                "(caps.check()를 거치지 않은 의도는 타입이 없다)"
            )
        if self._live:
            receipt = official_order.preview(
                self._client,
                intent_hash=ci.intent_hash, symbol=ci.symbol, side=ci.side,
                quantity=ci.quantity, amount=ci.amount_krw,
                ttl_s=self._ttl, clock=self._clock,
            )
        else:
            price = self._quotes(ci.symbol)
            exec_price = (
                self.costs.buy_price(price) if ci.side == "BUY"
                else self.costs.sell_price(price)
            )
            qty = (
                ci.quantity if ci.quantity is not None
                else ci.amount_krw / exec_price
            )
            notional = qty * exec_price
            receipt = PaperReceipt(
                intent_hash=ci.intent_hash, symbol=ci.symbol, side=ci.side,
                qty=qty, exec_price=exec_price,
                commission=self.costs.commission(notional),
                tax=self.costs.sell_tax(notional) if ci.side == "SELL" else 0.0,
                expires_at=self._clock() + self._ttl,
                receipt_hash=_paper_hash(
                    ci.intent_hash, ci.symbol, ci.side, qty, exec_price
                ),
                _seal=_PAPER_SEAL,
            )
        self._registry.append_event(EVENT_PREVIEW, "info", {
            "intent_hash": ci.intent_hash, "symbol": ci.symbol, "side": ci.side,
            "live": self._live,
        })
        return receipt

    # ── execute — 영수증만 입장 ────────────────────────────────────────

    def execute(self, receipt) -> dict:
        if isinstance(receipt, official_order.PreviewReceipt):
            if not self._live:
                raise GateError("live_trading=false — 실물 영수증은 집행 불가 (§I3)")
            official_order.execute(self._client, receipt, clock=self._clock)
            raise AssertionError("unreachable — 실물 분기는 Phase 7까지 부재")
        if not isinstance(receipt, PaperReceipt):
            raise ReceiptRequired(
                f"execute는 preview 영수증만 받는다 — {type(receipt).__name__} 불가 "
                "(str token 오버로드는 존재하지 않는다, §I3)"
            )
        if receipt.receipt_hash != _paper_hash(
            receipt.intent_hash, receipt.symbol, receipt.side,
            receipt.qty, receipt.exec_price,
        ):
            raise GateError("영수증 필드가 preview 시점과 다르다 — 변조 거부")
        if self._clock() > receipt.expires_at:
            raise GateError("영수증 만료 — preview부터 다시")

        # 페이퍼 체결 (백테스트 sim과 동일 산식)
        p = self._paper
        notional = receipt.qty * receipt.exec_price
        if receipt.side == "BUY":
            total = notional + receipt.commission
            if total > p.cash + 1e-9:
                raise GateError(f"페이퍼 현금 부족: {p.cash:.0f} < {total:.0f}")
            p.cash -= total
            prev_q = p.qty.get(receipt.symbol, 0.0)
            new_q = prev_q + receipt.qty
            p.avg_cost[receipt.symbol] = (
                p.avg_cost.get(receipt.symbol, 0.0) * prev_q
                + receipt.exec_price * receipt.qty
            ) / new_q
            p.qty[receipt.symbol] = new_q
        else:
            held = p.qty.get(receipt.symbol, 0.0)
            if receipt.qty > held + 1e-9:
                raise GateError(f"페이퍼 보유 부족: {held} < {receipt.qty}")
            p.cash += notional - receipt.commission - receipt.tax
            p.qty[receipt.symbol] = held - receipt.qty
            if p.qty[receipt.symbol] <= 1e-12:
                del p.qty[receipt.symbol]
                p.avg_cost.pop(receipt.symbol, None)

        fill = {
            "intent_hash": receipt.intent_hash, "symbol": receipt.symbol,
            "side": receipt.side, "qty": receipt.qty,
            "exec_price": receipt.exec_price, "commission": receipt.commission,
            "tax": receipt.tax,
        }
        self._registry.append_order(
            receipt.intent_hash, ORDER_STATUS_PAPER_FILLED, fill
        )
        return fill
