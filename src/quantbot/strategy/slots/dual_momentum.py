"""dual_momentum 슬롯 (STRAT v1.4, Antonacci 계열) — 자산군 로테이션, 순수 함수.

상대 모멘텀(자산군 ETF 간 순위 상위 top_n) ∩ 절대 모멘텀(수익률 > 0).
절대 필터를 통과하는 자산이 없으면 전액 현금 — 이 "현금 도피"가 이 계열의
MDD 방어 핵심이고, 문헌(추세추종·듀얼 모멘텀)에서 가장 재현성 높은 부분이다.

trend_score와 동일 수식을 재사용한다(스킵 포함) — 지식은 한 곳에 산다.
"""

from __future__ import annotations

from typing import Iterable, Mapping

import numpy as np

from quantbot.strategy.slots import rules, trend_score


def dual_momentum_select(
    closes: Mapping[str, np.ndarray],
    lookback: int,
    top_n: int,
    skip: int = 0,
    holdings: Iterable[str] = (),
    exit_buffer: float = 1.0,
) -> list[str]:
    """상대 상위 top_n ∩ 절대(>0) — 미달분은 선택하지 않는다(= 현금).

    히스테리시스 (v2, 2026-07-07 실측 교훈 — 회전율 5.9x): 보유 자산은
    순위가 top_n × exit_buffer 밖으로 밀리거나 절대 모멘텀이 음전할 때만
    교체한다. 진입은 여전히 절대 필터(>0) 통과 자산만 — rules.select_holdings
    (주식 슬롯과 같은 규칙)를 재사용해 지식은 한 곳에 산다.
    """
    if top_n < 1:
        raise ValueError(f"top_n ≥ 1: {top_n}")
    mom = trend_score.momentum(closes, lookback=lookback, skip=skip)
    positive = trend_score.apply_abs_filter(mom, True)  # 절대 필터 — 음전은 순위 밖
    ranked = trend_score.rank_desc(positive)
    return rules.select_holdings(
        ranked, holdings, top_n, exit_buffer,
        scores=positive, require_positive_score=True,
    )
