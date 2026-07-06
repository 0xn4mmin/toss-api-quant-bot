"""rank_top_n / rank_drop 슬롯 (SIG-02/04) — 진입·청산 규칙, 순수 함수.

히스테리시스: 진입선(상위 n)과 청산선(rank > n × exit_buffer)을 분리해
순위 경계에서의 왕복 매매를 막는다 — 소액 계좌 수수료 드래그(RISK-02) 1차 방어.
"""

from __future__ import annotations

from typing import Iterable, Mapping


def select_holdings(
    ranked: list[str],
    holdings: Iterable[str],
    n: int,
    exit_buffer: float,
    scores: Mapping[str, float] | None = None,
    require_positive_score: bool = False,
) -> list[str]:
    """§S2 의사코드: keep = 보유 중 rank ≤ n×exit_buffer, entry = 빈 슬롯을 상위로.

    require_positive_score=True 면 keep에도 score > 0 을 요구한다
    (KR sleeve의 '수급 순유출 전환 시 청산', §S4).
    """
    if n < 1 or exit_buffer < 1.0:
        raise ValueError(f"n ≥ 1, exit_buffer ≥ 1: {n}, {exit_buffer}")
    rank_of = {s: k + 1 for k, s in enumerate(ranked)}
    exit_line = n * exit_buffer
    keep = []
    for s in sorted(holdings):
        if s not in rank_of or rank_of[s] > exit_line:
            continue  # 순위 이탈 → 청산
        if require_positive_score and scores is not None and scores.get(s, 0.0) <= 0.0:
            continue  # 순유출 전환 → 청산
        keep.append(s)
    entries = [s for s in ranked if s not in keep][: max(n - len(keep), 0)]
    return sorted(keep + entries, key=lambda s: rank_of.get(s, 10**9))
