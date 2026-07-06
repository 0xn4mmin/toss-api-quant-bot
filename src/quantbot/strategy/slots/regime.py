"""regime_filter 슬롯 (SIG-05) — 3단 이산 노출 스위치, 순수 함수.

trend_ok = 지수 > MA(ma_len), vol_ok = VIX < threshold.
둘 다 → 1.0(risk-on), 하나만 → caution_exposure(스펙상 0.5 고정 — 탐색 금지,
값은 전략 파일이 선언), 둘 다 아님 → e_min(risk-off 잔여 노출).
자유 파라미터를 3단으로 줄인 이유 = 과최적화 표면적 최소화 (OF-02).
"""

from __future__ import annotations

import numpy as np


def regime_exposure(
    index_close: np.ndarray,
    vix_level: float,
    ma_len: int,
    vix_threshold: float,
    e_min: float,
    caution_exposure: float,
) -> float:
    """§S4 의사코드 그대로 — sleeve 목표 비중에 곱해질 노출값."""
    c = np.asarray(index_close, dtype=float)
    if len(c) < ma_len:
        raise ValueError(f"지수 이력 {len(c)} < ma_len {ma_len} — 판정 불가")
    if not (0.0 <= e_min <= caution_exposure <= 1.0):
        raise ValueError(f"0 ≤ e_min({e_min}) ≤ caution({caution_exposure}) ≤ 1")
    trend_ok = c[-1] > float(np.mean(c[-ma_len:]))
    vol_ok = vix_level < vix_threshold
    if trend_ok and vol_ok:
        return 1.0
    if trend_ok or vol_ok:
        return caution_exposure
    return e_min
