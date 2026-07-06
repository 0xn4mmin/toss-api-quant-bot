"""trend_score 슬롯 (SIG-01/02) — 횡단면 모멘텀, 순수 함수.

mom[s] = close[t−skip] / close[t−skip−lookback] − 1, 이후 유니버스 내 순위화.
순위 기반인 이유: 수익률 절대값보다 순위가 분포 이상치에 강건하다 (§S2).
데이터가 짧은 종목은 조용히 0을 주지 않고 제외한다.
"""

from __future__ import annotations

from typing import Mapping

import numpy as np


def momentum(
    closes: Mapping[str, np.ndarray], lookback: int, skip: int
) -> dict[str, float]:
    """종목별 모멘텀 — close[-1-skip] / close[-1-skip-lookback] - 1 (§S2 의사코드)."""
    if lookback < 1 or skip < 0:
        raise ValueError(f"lookback ≥ 1, skip ≥ 0: {lookback}, {skip}")
    out: dict[str, float] = {}
    need = lookback + skip + 1
    for symbol in sorted(closes):
        c = np.asarray(closes[symbol], dtype=float)
        if len(c) < need:
            continue  # 이력 부족 — 시그널 없음 (0이 아니라 부재)
        out[symbol] = float(c[-1 - skip] / c[-1 - skip - lookback] - 1.0)
    return out


def rank_desc(scores: Mapping[str, float]) -> list[str]:
    """점수 내림차순 순위 — 동점은 심볼 순으로 결정적."""
    return sorted(scores, key=lambda s: (-scores[s], s))


def apply_abs_filter(scores: Mapping[str, float], enabled: bool) -> dict[str, float]:
    """절대 모멘텀 필터 (탐색: on/off) — mom > 0 만 통과."""
    if not enabled:
        return dict(scores)
    return {s: v for s, v in scores.items() if v > 0.0}
