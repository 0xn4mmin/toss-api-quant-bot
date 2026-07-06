"""슬롯 조립 — 전략 선언(dict)을 시그널 함수로 잇는 순수 클로저 (SIG-02→06).

전략 계층은 캡을 선제 존중한다(클리핑+재배분) — 엔진 불변식(INV-01)은 최후
방어선이고, 정상 경로에서 발동하면 그것은 전략 계층의 버그다 (§S5).
cap 값은 이 계층이 invariants를 읽는 게 아니라 호출자(엔진/러너)가 주입한다 —
전략은 불변식을 만질 인터페이스가 없다.

상태: 히스테리시스(진입/청산선 분리)를 위해 클로저가 보유 목록을 기억한다.
같은 입력 시퀀스 → 같은 출력 시퀀스 (결정적).
"""

from __future__ import annotations

from typing import Mapping

import numpy as np

from quantbot.strategy.slots import regime, rules, trend_score


def cap_clip_redistribute(weights: dict[str, float], cap: float) -> dict[str, float]:
    """§S5 2단: cap 초과분을 캡 미만 종목에 비례 재배분, 전원 캡이면 현금으로.

    매 반복마다 최소 한 종목이 캡에 영구 고정되므로 종료가 보장된다.
    """
    if cap <= 0:
        raise ValueError(f"cap > 0 필요: {cap}")
    w = {s: v for s, v in sorted(weights.items()) if v > 0}
    while True:
        over = {s for s, v in w.items() if v > cap + 1e-12}
        if not over:
            return w
        excess = sum(w[s] - cap for s in over)
        for s in over:
            w[s] = cap
        under = {s for s, v in w.items() if v < cap - 1e-12}
        under_total = sum(w[s] for s in under)
        if not under or under_total <= 0:
            return w  # 전원 캡 — 잔여(excess)는 현금으로 남는다
        for s in sorted(under):
            w[s] = w[s] + excess * (w[s] / under_total)


def build_us_core_signal(
    params: Mapping[str, object],
    cap: float,
    index_symbol: str | None = None,
    vix_symbol: str | None = None,
):
    """US 코어 시그널 함수 (SIG-02) — Phase 1 러너의 SignalFn 시그니처와 호환.

    params: lookback, skip, abs_filter, n, exit_buffer
            (+ 레짐: ma_len, vix_threshold, e_min, caution_exposure — index/vix 주입 시)
    index_symbol/vix_symbol: 뷰 안에서 유니버스가 아니라 레짐 입력으로 취급할 심볼.
    """
    holdings: set[str] = set()
    excluded = {s for s in (index_symbol, vix_symbol) if s}

    def signal(view, p_override: Mapping[str, object] | None = None) -> dict[str, float]:
        p = dict(params)
        if p_override:
            p.update(p_override)
        symbols = [s for s in view.symbols if s not in excluded]
        closes = {s: view.close(s) for s in symbols}
        mom = trend_score.momentum(closes, int(p["lookback"]), int(p["skip"]))
        mom = trend_score.apply_abs_filter(mom, bool(p.get("abs_filter", False)))
        ranked = trend_score.rank_desc(mom)
        selection = rules.select_holdings(
            ranked, holdings, int(p["n"]), float(p["exit_buffer"])
        )
        holdings.clear()
        holdings.update(selection)
        if not selection:
            return {}
        exposure = 1.0
        if index_symbol is not None and vix_symbol is not None:
            exposure = regime.regime_exposure(
                view.close(index_symbol),
                float(np.asarray(view.close(vix_symbol))[-1]),
                int(p["ma_len"]),
                float(p["vix_threshold"]),
                float(p["e_min"]),
                float(p["caution_exposure"]),
            )
        if exposure <= 0.0:
            return {}
        equal = exposure / len(selection)   # §S5 1단: sleeve 내 동일가중
        return cap_clip_redistribute({s: equal for s in selection}, cap)

    return signal
