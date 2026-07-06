"""dual_momentum 슬롯 (STRAT v1.4, Antonacci 계열) — 자산군 로테이션, 순수 함수.

상대 모멘텀(자산군 ETF 간 순위 상위 top_n) ∩ 절대 모멘텀(수익률 > 0).
절대 필터를 통과하는 자산이 없으면 전액 현금 — 이 "현금 도피"가 이 계열의
MDD 방어 핵심이고, 문헌(추세추종·듀얼 모멘텀)에서 가장 재현성 높은 부분이다.

trend_score와 동일 수식을 재사용한다(스킵 포함) — 지식은 한 곳에 산다.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np

from quantbot.strategy.slots import trend_score


def dual_momentum_select(
    closes: Mapping[str, np.ndarray],
    lookback: int,
    top_n: int,
    skip: int = 0,
) -> list[str]:
    """상대 상위 top_n ∩ 절대(>0) — 미달분은 선택하지 않는다(= 현금)."""
    if top_n < 1:
        raise ValueError(f"top_n ≥ 1: {top_n}")
    mom = trend_score.momentum(closes, lookback=lookback, skip=skip)
    ranked = trend_score.rank_desc(mom)
    return [a for a in ranked[:top_n] if mom[a] > 0.0]
