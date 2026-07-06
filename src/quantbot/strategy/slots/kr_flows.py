"""kr_flows_score 슬롯 (SIG-03/04) — 수급 강도 × 지속성, 순수 함수.

score = Σ(외국인+기관 순매수, W일) ÷ Σ(거래대금, W일)  — 대형주 편향 제거
persistence = W일 중 순매수 > 0 인 일수 비율                — 스파이크 노이즈 제거
입력은 적재기(IMPL-06)가 쌓은 flows 행 — 백테스트는 as_of 뷰, live는 최신 뷰,
같은 스토어를 본다 (§I6 데이터 경로 동일성).
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np


def flows_score(
    foreign_net: Sequence[float],
    inst_net: Sequence[float],
    traded_value: Sequence[float],
    window: int,
) -> tuple[float, float] | None:
    """(score, persistence) — 표본이 W일 미만이면 None (0이 아니라 부재)."""
    f = np.asarray(foreign_net, dtype=float)
    i = np.asarray(inst_net, dtype=float)
    tv = np.asarray(traded_value, dtype=float)
    if not (len(f) == len(i) == len(tv)):
        raise ValueError("flows 배열 길이 불일치")
    if len(f) < window or window < 1:
        return None
    f, i, tv = f[-window:], i[-window:], tv[-window:]
    total_tv = float(tv.sum())
    if total_tv <= 0:
        return None
    net = f + i
    score = float(net.sum()) / total_tv
    persistence = float((net > 0).mean())
    return score, persistence


def select_candidates(
    scores: Mapping[str, tuple[float, float]],
    p_min: float,
) -> dict[str, float]:
    """지속성 하한 통과 종목의 score만 남긴다 (§S4: candidates)."""
    return {
        s: sp[0] for s, sp in sorted(scores.items()) if sp[1] >= p_min
    }
