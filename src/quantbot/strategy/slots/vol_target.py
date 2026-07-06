"""변동성 타게팅 오버레이 (STRAT v1.4, Moreira-Muir 2017 계열) — 순수 함수.

노출 스칼라 = min(1, 목표 연변동성 / 실현 연변동성). 변동성이 목표보다 높으면
노출을 줄이고, 낮아도 1.0을 넘지 않는다 — 레버리지 금지(INV-02)가 상한이다.
목표·룩백은 성과 그리드가 아니라 전략 확정값 (OF-03: 사이징 탐색 금지).

표본이 부족하거나 변동성이 0이면 스케일하지 않는다(1.0) — 측정 불가 시
노출을 늘리는 방향의 오류가 없도록 상한이 1.0인 구조 자체가 방어다.
"""

from __future__ import annotations

import numpy as np


def realized_vol_annual(returns: np.ndarray, trading_days_per_year: int) -> float:
    """실현 연변동성 — 일일 수익률 표준편차 × √연간 거래일."""
    r = np.asarray(returns, dtype=float)
    if len(r) < 2:
        return 0.0
    return float(np.std(r, ddof=1) * np.sqrt(trading_days_per_year))


def vol_target_scalar(
    closes: np.ndarray,
    annual_target: float,
    lookback_days: int,
    trading_days_per_year: int,
) -> float:
    """포트폴리오(또는 프록시 자산) 종가열 → 노출 스칼라 ∈ (0, 1].

    §S5 v1.4: scalar = min(1, target / realized). 이력 < lookback이면 1.0.
    """
    if not (0.0 < annual_target <= 1.0):
        raise ValueError(f"annual_target ∈ (0, 1]: {annual_target}")
    if lookback_days < 2:
        raise ValueError(f"lookback_days ≥ 2: {lookback_days}")
    c = np.asarray(closes, dtype=float)
    if len(c) < lookback_days + 1:
        return 1.0  # 측정 불가 — 스케일하지 않음 (상한 1.0이 구조적 방어)
    window = c[-(lookback_days + 1):]
    returns = window[1:] / window[:-1] - 1.0
    realized = realized_vol_annual(returns, trading_days_per_year)
    if realized <= 0.0:
        return 1.0
    return min(1.0, annual_target / realized)
