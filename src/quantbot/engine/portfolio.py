"""목표 비중·no-trade band·회전율·단계 전환 (SIG-06/07) — 순수 계산.

3단 파이프라인(§S5): ① sleeve 내 동일가중(전략 계층, slots.pipeline이 수행)
② 12% 캡 클리핑+재배분(전략 계층 선제 존중 — 같은 구현을 재사용)
③ no-trade band — 변화가 작으면 주문 생략 (RISK-02/05).
캡 클리핑 구현은 strategy.slots.pipeline의 것을 그대로 쓴다 — 전략 계층이
선제 존중하는 규칙과 엔진이 계산하는 규칙이 한 코드 계보를 갖게.
"""

from __future__ import annotations

from typing import Mapping

from quantbot.strategy.slots.pipeline import cap_clip_redistribute

__all__ = [
    "cap_clip_redistribute",
    "no_trade_band_orders",
    "turnover",
    "staged_transition_plan",
]


def no_trade_band_orders(
    current: Mapping[str, float],
    target: Mapping[str, float],
    band: float,
) -> dict[str, float]:
    """|현재 − 목표| > band 인 종목만 목표 비중으로 — 나머지는 주문 생략 (§S5 3단)."""
    if band < 0:
        raise ValueError(f"band ≥ 0: {band}")
    orders: dict[str, float] = {}
    for s in sorted(set(current) | set(target)):
        cur, tgt = current.get(s, 0.0), target.get(s, 0.0)
        if abs(cur - tgt) > band:
            orders[s] = tgt
    return orders


def turnover(w_old: Mapping[str, float], w_new: Mapping[str, float]) -> float:
    """전환 회전율 = Σ|w_new − w_old| / 2 (SIG-07)."""
    symbols = set(w_old) | set(w_new)
    return sum(abs(w_new.get(s, 0.0) - w_old.get(s, 0.0)) for s in symbols) / 2.0


def staged_transition_plan(
    w_old: Mapping[str, float],
    w_new: Mapping[str, float],
    max_turnover_per_step: float,
) -> list[dict[str, float]]:
    """전환을 k번의 주간 리밸런싱으로 쪼개 매 단계 회전율 ≤ 임계가 되게 보간 (SIG-07).

    선형 보간: k = ceil(총회전율 / 임계). 각 단계의 회전율은 총회전율/k ≤ 임계.
    """
    if max_turnover_per_step <= 0:
        raise ValueError(f"임계 > 0 필요: {max_turnover_per_step}")
    total = turnover(w_old, w_new)
    if total <= max_turnover_per_step:
        return [dict(sorted(w_new.items()))]
    k = -(-total // max_turnover_per_step)  # ceil
    k = int(k)
    symbols = sorted(set(w_old) | set(w_new))
    plan = []
    for step in range(1, k + 1):
        frac = step / k
        stage = {}
        for s in symbols:
            w = w_old.get(s, 0.0) + (w_new.get(s, 0.0) - w_old.get(s, 0.0)) * frac
            if w > 1e-12:
                stage[s] = w
        plan.append(stage)
    return plan
