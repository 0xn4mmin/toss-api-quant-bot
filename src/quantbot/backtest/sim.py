"""시뮬레이터 (IMPL-05) — 시점 인덱스로 look-ahead를 강제 차단 (BT-D3).

시그널은 시점 t의 as_of(t) 뷰만 받고(t 종가까지), 집행은 t+1 종가 ± 슬리피지.
체결 모델은 §I3의 PaperBroker가 Phase 4에서 공유할 코드 계보다.
전략(signal_fn)은 주입받는 순수 함수 — 시뮬레이터는 전략 내용을 모른다.

결정성: 동일 입력 → 바이트 동일 출력. 시각·난수를 쓰지 않고 종목 순회는 정렬 순서.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from quantbot.backtest.config import Methodology
from quantbot.backtest.costs import CostModel
from quantbot.backtest.data import AsOfView, MarketDataStore

SignalFn = Callable[[AsOfView, dict], dict[str, float]]
"""(as_of 뷰, params) → {symbol: 목표 비중}. 미포함 종목은 비중 0."""


class SimError(ValueError):
    pass


@dataclass(frozen=True)
class TradeRecord:
    """매도 체결의 실현 손익 기록 (승률·손익비 근거)."""

    date: str
    symbol: str
    qty: float
    realized_pnl: float


@dataclass
class SimResult:
    dates: list[str] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    order_notionals: list[float] = field(default_factory=list)
    total_costs: float = 0.0
    total_taxes: float = 0.0
    trades: list[TradeRecord] = field(default_factory=list)
    exposure_days: int = 0

    def returns(self) -> np.ndarray:
        eq = np.asarray(self.equity)
        if len(eq) < 2:
            return np.zeros(0)
        return eq[1:] / eq[:-1] - 1.0


ORDER_UNITS = ("fractional", "whole")


def simulate(
    store: MarketDataStore,
    start_idx: int,
    end_idx: int,
    signal_fn: SignalFn,
    params: dict,
    cost_model: CostModel,
    m: Methodology,
    order_unit: str = "fractional",
    no_trade_band: float = 0.0,
) -> SimResult:
    """[start_idx, end_idx] 구간을 재생한다 (양끝 포함).

    리밸런싱: 구간 시작일부터 m.rebalance_every_n_days 마다 시그널 계산(t),
    집행은 t+1 종가. 레버리지 0: 매수는 가용 현금 한도로 축소된다.

    order_unit (ARCH v1.1 결정 2): 전략 선언 단위 그대로 평가한다 —
    whole = 정수 수량 집행(절사에 의한 비중 왜곡·현금 잔류를 그대로 반영),
    fractional = 금액 기반 배분(수량 소수점 허용).

    no_trade_band (§S5 3단, 2026-07-07 충실도 교정): |변화 비중| ≤ band 인
    주문은 생략 — 실운용(portfolio.no_trade_band_orders)과 동일 규칙. 이게
    없으면 백테스트가 매주 드리프트 매매까지 세어 회전율·비용을 과대평가한다.
    """
    if no_trade_band < 0:
        raise SimError(f"no_trade_band ≥ 0: {no_trade_band}")
    if order_unit not in ORDER_UNITS:
        raise SimError(f"order_unit ∈ {ORDER_UNITS}: {order_unit!r}")
    if not (0 <= start_idx <= end_idx < len(store)):
        raise SimError(f"구간 오류: [{start_idx}, {end_idx}] / len={len(store)}")

    def snap(q: float) -> float:
        return float(int(q)) if order_unit == "whole" else q

    cash = m.initial_capital_krw
    qty: dict[str, float] = {}
    avg_cost: dict[str, float] = {}
    pending: dict[str, float] | None = None
    result = SimResult()
    realized_this_year = 0.0

    def mark(i: int) -> float:
        return cash + sum(q * store.close_at(s, i) for s, q in sorted(qty.items()))

    for i in range(start_idx, end_idx + 1):
        # 1) 직전 리밸런싱의 목표를 오늘 종가로 집행 (t+1 집행, BT-D3)
        if pending is not None:
            eq_now = mark(i)
            # 매도 먼저 (현금 확보), 그다음 매수 — 각각 정렬 순서로 결정적
            for s in sorted(set(qty) | set(pending)):
                target_w = pending.get(s, 0.0)
                cur_q = qty.get(s, 0.0)
                px = store.close_at(s, i)
                target_q = snap((target_w * eq_now) / cost_model.buy_price(px))
                if (cur_q - target_q) * px <= no_trade_band * eq_now:
                    pass  # 변화 비중이 밴드 안 — 주문 생략 (§S5 3단)
                elif cur_q - target_q > 0:
                    sell_q = cur_q - target_q
                    exec_px = cost_model.sell_price(px)
                    notional = sell_q * exec_px
                    fee = cost_model.commission(notional)
                    tax = cost_model.sell_tax(notional)
                    cash += notional - fee - tax
                    pnl = (exec_px - avg_cost.get(s, exec_px)) * sell_q - fee - tax
                    realized_this_year += pnl
                    result.trades.append(
                        TradeRecord(store.date(i), s, sell_q, pnl)
                    )
                    result.order_notionals.append(notional)
                    result.total_costs += fee + tax
                    qty[s] = cur_q - sell_q
                    if qty[s] <= 0:
                        del qty[s]
                        avg_cost.pop(s, None)
            for s in sorted(pending):
                target_w = pending[s]
                if target_w <= 0:
                    continue
                cur_q = qty.get(s, 0.0)
                px = store.close_at(s, i)
                exec_px = cost_model.buy_price(px)
                target_q = snap((target_w * eq_now) / exec_px)
                buy_q = target_q - cur_q
                if buy_q <= 0 or buy_q * exec_px <= no_trade_band * eq_now:
                    continue  # 밴드 안 — 주문 생략 (§S5 3단)
                notional = buy_q * exec_px
                fee = cost_model.commission(notional)
                if notional + fee > cash:  # 레버리지 0 — 현금 한도로 축소
                    buy_q = snap(max(cash - fee, 0.0) / exec_px)
                    notional = buy_q * exec_px
                if buy_q <= 0 or notional <= 0:
                    continue
                cash -= notional + fee
                new_q = cur_q + buy_q
                avg_cost[s] = (avg_cost.get(s, 0.0) * cur_q + exec_px * buy_q) / new_q
                qty[s] = new_q
                result.order_notionals.append(notional)
                result.total_costs += fee
            pending = None

        # 2) 평가
        eq = mark(i)
        result.dates.append(store.date(i))
        result.equity.append(eq)
        if qty:
            result.exposure_days += 1

        # 3) 리밸런싱 날이면 시그널 계산 — 전략은 as_of(i) 뷰만 받는다
        if (i - start_idx) % m.rebalance_every_n_days == 0 and i < end_idx:
            targets = signal_fn(store.as_of(i), params)
            total_w = sum(targets.values())
            if total_w > 1.0 + 1e-9:
                raise SimError(f"목표 비중 합 {total_w:.4f} > 1 — 레버리지 금지(INV-02)")
            if any(w < 0 for w in targets.values()):
                raise SimError("음수 비중 — 공매도 금지(INV-02)")
            pending = dict(sorted(targets.items()))

        # 4) 연말 정산 (US형 양도세)
        year_ends = i == end_idx or store.date(i)[:4] != store.date(i + 1)[:4]
        if year_ends and realized_this_year > 0:
            tax = cost_model.annual_tax(realized_this_year)
            if tax > 0:
                cash -= tax
                result.total_taxes += tax
                result.equity[-1] = mark(i)
            realized_this_year = 0.0

    return result


# ── 성과 통계 (BT-06) — 순수 함수, 연환산 계수는 config 주입 ────────────


def mdd(equity: np.ndarray) -> float:
    eq = np.asarray(equity, dtype=float)
    if len(eq) == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float(np.max((peak - eq) / peak))


def mdd_recovery_days(equity: np.ndarray) -> int:
    """최대 낙폭의 회복 소요 일수. 미회복이면 곡선 끝까지의 일수."""
    eq = np.asarray(equity, dtype=float)
    if len(eq) == 0:
        return 0
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak
    trough = int(np.argmax(dd))
    peak_val = peak[trough]
    for j in range(trough, len(eq)):
        if eq[j] >= peak_val:
            return j - trough
    return len(eq) - 1 - trough


def sharpe(returns: np.ndarray, trading_days_per_year: int) -> float:
    r = np.asarray(returns, dtype=float)
    if len(r) < 2 or float(np.std(r, ddof=1)) == 0.0:
        return 0.0
    return float(np.mean(r) / np.std(r, ddof=1) * np.sqrt(trading_days_per_year))


def cagr(equity: np.ndarray, trading_days_per_year: int) -> float:
    eq = np.asarray(equity, dtype=float)
    if len(eq) < 2 or eq[0] <= 0:
        return 0.0
    years = (len(eq) - 1) / trading_days_per_year
    if eq[-1] <= 0:
        return -1.0
    return float((eq[-1] / eq[0]) ** (1.0 / years) - 1.0)


def worst_month_return(dates: list[str], equity: np.ndarray) -> float:
    eq = np.asarray(equity, dtype=float)
    if len(eq) < 2:
        return 0.0
    worst = 0.0
    month_start_val = eq[0]
    for i in range(1, len(eq)):
        if dates[i][:7] != dates[i - 1][:7]:
            worst = min(worst, float(eq[i - 1] / month_start_val - 1.0))
            month_start_val = eq[i - 1]
    worst = min(worst, float(eq[-1] / month_start_val - 1.0))
    return worst


def equity_from_returns(returns: np.ndarray, initial: float) -> np.ndarray:
    return initial * np.cumprod(np.concatenate([[1.0], 1.0 + np.asarray(returns)]))
