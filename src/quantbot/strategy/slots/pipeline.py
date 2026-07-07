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

from quantbot.strategy.slots import dual_momentum, regime, rules, trend_score, vol_target

VolTarget = tuple[float, int, int]  # (연 목표 변동성, 룩백 거래일, 연간 거래일)


class _ScalarSmoother:
    """스칼라 변경 밴드 — 작은 변동은 직전 노출 유지 (회전율 억제, 선언값).

    band=None이면 통과. 상태는 클로저 수명 = 시뮬레이션 1회 수명 (결정적).
    """

    def __init__(self, band: float | None) -> None:
        self._band = band
        self._last: float | None = None

    def smooth(self, scalar: float) -> float:
        if self._band is None:
            return scalar
        if self._last is not None and abs(scalar - self._last) <= self._band:
            return self._last
        self._last = scalar
        return scalar


def _basket_vol_scalar(
    closes_by_symbol: Mapping[str, np.ndarray], vt: VolTarget
) -> float:
    """선택 종목 동일가중 바스켓의 실현변동성으로 노출 스칼라 산출 (§S5 v1.4)."""
    annual_target, lookback, tdpy = vt
    need = lookback + 1
    usable = [np.asarray(c, dtype=float)[-need:]
              for c in closes_by_symbol.values() if len(c) >= need]
    if not usable:
        return 1.0  # 측정 불가 — 스케일하지 않음 (상한 1.0이 구조적 방어)
    rets = np.mean([c[1:] / c[:-1] - 1.0 for c in usable], axis=0)
    realized = vol_target.realized_vol_annual(rets, tdpy)
    if realized <= 0.0:
        return 1.0
    return min(1.0, annual_target / realized)


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
    vol_target_spec: VolTarget | None = None,
    vol_scalar_band: float | None = None,
):
    """US 코어 시그널 함수 (SIG-02) — Phase 1 러너의 SignalFn 시그니처와 호환.

    params: lookback, skip, abs_filter, n, exit_buffer
            (+ 레짐: ma_len, vix_threshold, e_min, caution_exposure — index/vix 주입 시)
    index_symbol/vix_symbol: 뷰 안에서 유니버스가 아니라 레짐 입력으로 취급할 심볼.
    """
    holdings: set[str] = set()
    excluded = {s for s in (index_symbol, vix_symbol) if s}
    smoother = _ScalarSmoother(vol_scalar_band)

    def signal(view, p_override: Mapping[str, object] | None = None) -> dict[str, float]:
        p = dict(params)
        if p_override:
            p.update(p_override)
        # 레짐 판정 불가(지수 이력 < ma_len) = 노출 안 함 — 워크포워드 워밍업의
        # 정상 조건이며, 측정 못 한 위험은 지지 않는다 (fail-closed)
        if index_symbol is not None and vix_symbol is not None:
            if len(view.close(index_symbol)) < int(p["ma_len"]):
                return {}
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
        if vol_target_spec is not None:  # v1.4 오버레이 — 스칼라 ≤ 1이라 캡 보존
            exposure *= smoother.smooth(_basket_vol_scalar(
                {s: closes[s] for s in selection}, vol_target_spec
            ))
        if exposure <= 0.0:
            return {}
        equal = exposure / len(selection)   # §S5 1단: sleeve 내 동일가중
        return cap_clip_redistribute({s: equal for s in selection}, cap)

    return signal


def build_dual_momentum_signal(
    params: Mapping[str, object],
    cap: float,
    vol_target_spec: VolTarget | None = None,
    vol_scalar_band: float | None = None,
):
    """자산군 듀얼 모멘텀 시그널 (STRAT v1.4) — Phase 1 러너 SignalFn 호환.

    params: lookback, top_n, skip(선택). 비중은 1/top_n — 절대 필터에 걸린
    슬롯은 현금으로 남는다(Antonacci의 현금 도피가 MDD 방어의 핵심).
    주의: 단일 ETF 고비중은 INV-01(12% 캡)과 상호작용한다 — 캡은 호출자
    (엔진 invariants)가 주입하고, 클리핑 잔여는 현금이 된다.
    """
    smoother = _ScalarSmoother(vol_scalar_band)

    def signal(view, p_override: Mapping[str, object] | None = None) -> dict[str, float]:
        p = dict(params)
        if p_override:
            p.update(p_override)
        closes = {s: view.close(s) for s in view.symbols}
        top_n = int(p["top_n"])
        selection = dual_momentum.dual_momentum_select(
            closes, lookback=int(p["lookback"]), top_n=top_n,
            skip=int(p.get("skip", 0)),
        )
        if not selection:
            return {}  # 절대 필터 전멸 — 전액 현금
        exposure = 1.0
        if vol_target_spec is not None:
            exposure = smoother.smooth(_basket_vol_scalar(
                {s: closes[s] for s in selection}, vol_target_spec
            ))
        per_asset = exposure / top_n  # 미선택 슬롯 = 현금
        return cap_clip_redistribute({s: per_asset for s in selection}, cap)

    return signal
