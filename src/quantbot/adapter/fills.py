"""체결·비용 모델 (BT-05, §I3) — 백테스트·페이퍼·live가 공유하는 단일 계보.

백테스트 시뮬레이터(backtest.sim)와 페이퍼 게이트(engine.gate)가 같은 산식을
쓴다: 슬리피지는 항상 불리한 방향, 수수료는 하한 있는 정률, 매도세, 연말 양도세.
수치는 전부 config/backtest.yaml costs 섹션 주입 — 실측(공식 commissions·
orderbook) 전에는 보수 방향 기본값.
"""

from __future__ import annotations

from dataclasses import dataclass


class CostConfigError(ValueError):
    """비용 설정 형식 오류."""


@dataclass(frozen=True)
class CostModel:
    commission_rate: float
    min_commission_krw: float
    slippage_rate: float
    sell_tax_rate: float
    annual_gain_tax_rate: float
    annual_deduction_krw: float

    @classmethod
    def from_config(cls, cfg: dict) -> "CostModel":
        vals = {}
        for key in (
            "commission_rate",
            "min_commission_krw",
            "slippage_rate",
            "sell_tax_rate",
            "annual_gain_tax_rate",
            "annual_deduction_krw",
        ):
            v = cfg.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise CostConfigError(f"costs.{key}: 0 이상 숫자여야 한다: {v!r}")
            vals[key] = float(v)
        return cls(**vals)

    def as_dict(self) -> dict:
        return {
            "commission_rate": self.commission_rate,
            "min_commission_krw": self.min_commission_krw,
            "slippage_rate": self.slippage_rate,
            "sell_tax_rate": self.sell_tax_rate,
            "annual_gain_tax_rate": self.annual_gain_tax_rate,
            "annual_deduction_krw": self.annual_deduction_krw,
        }

    # ── 체결가: 슬리피지는 항상 불리한 방향 ─────────────────────────
    def buy_price(self, close: float) -> float:
        return close * (1.0 + self.slippage_rate)

    def sell_price(self, close: float) -> float:
        return close * (1.0 - self.slippage_rate)

    # ── 주문 건별 비용 ─────────────────────────────────────────────
    def commission(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        return max(notional * self.commission_rate, self.min_commission_krw)

    def sell_tax(self, notional: float) -> float:
        return max(notional, 0.0) * self.sell_tax_rate

    # ── 연말 정산 (US형 양도세, BT-05) ──────────────────────────────
    def annual_tax(self, realized_gain: float) -> float:
        taxable = realized_gain - self.annual_deduction_krw
        if taxable <= 0:
            return 0.0
        return taxable * self.annual_gain_tax_rate
