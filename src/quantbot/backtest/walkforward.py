"""워크포워드 러너 (BT-03, BT-04) — IS에서만 탐색, OOS는 폴드당 1회 평가.

구조로 강제되는 것:
- 진입 관문이 prereg.require_seal — 봉인 없이/봉인과 다른 그리드로는 실행 자체가 안 된다.
- 시도한 (폴드 × 조합)마다 registry에 config_tried 이벤트를 append —
  n_configs_tried는 러너가 세는 값이지 보고자가 적는 값이 아니다 (BT-02/G7).
- 폴드는 rolling(anchored 아님): [train → test]를 step만큼 굴린다.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass

import numpy as np

from quantbot.backtest import prereg
from quantbot.backtest.config import Methodology
from quantbot.backtest.costs import CostModel
from quantbot.backtest.data import MarketDataStore
from quantbot.backtest.sim import SignalFn, SimResult, sharpe, simulate
from quantbot.engine.registry import Registry

EVENT_CONFIG_TRIED = "config_tried"


class WalkForwardError(ValueError):
    pass


@dataclass(frozen=True)
class Fold:
    train_start: int
    train_end: int  # exclusive
    test_start: int
    test_end: int  # exclusive


def make_folds(n_dates: int, m: Methodology) -> list[Fold]:
    folds = []
    start = 0
    while start + m.train_days + m.test_days <= n_dates:
        folds.append(
            Fold(
                train_start=start,
                train_end=start + m.train_days,
                test_start=start + m.train_days,
                test_end=start + m.train_days + m.test_days,
            )
        )
        start += m.step_days
    if not folds:
        raise WalkForwardError(
            f"데이터 {n_dates}일로는 폴드를 만들 수 없다 "
            f"(train {m.train_days} + test {m.test_days} 필요)"
        )
    return folds


def folds_spec(m: Methodology, order_unit: str = "fractional") -> dict:
    """사전등록에 봉인되는 폴드·집행 구성 (BT-02).

    order_unit이 봉인에 들어가므로 같은 id로 단위만 바꾸면 해시가 어긋난다 —
    단위 비교는 별개 전략 id의 별개 사전등록으로만 (STRAT v1.2 §S10).
    """
    return {
        "train_days": m.train_days,
        "test_days": m.test_days,
        "step_days": m.step_days,
        "rebalance_every_n_days": m.rebalance_every_n_days,
        "order_unit": order_unit,
    }


def grid_configs(grid: dict[str, list]) -> list[dict]:
    """정렬 키 순서의 데카르트 곱 — 순회가 결정적이다."""
    keys = sorted(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def grid_neighbors(grid: dict[str, list], config: dict) -> list[dict]:
    """정확히 한 축에서 인접 인덱스로 이동한 이웃들 (BT-G4 평탄 지대 판정용)."""
    neighbors = []
    for key in sorted(grid):
        values = grid[key]
        idx = values.index(config[key])
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(values):
                n = dict(config)
                n[key] = values[j]
                neighbors.append(n)
    return neighbors


def select_plateau_median(grid: dict[str, list], per_fold_best: list[dict]) -> dict:
    """BT-04 — 최종 파라미터는 단일 최고점이 아니라 폴드 간 선택의 축별 중앙값
    (그리드 값으로 스냅). bool 축은 다수결."""
    selected = {}
    for key in sorted(grid):
        values = grid[key]
        chosen = [c[key] for c in per_fold_best]
        if all(isinstance(v, bool) for v in values):
            selected[key] = max(set(chosen), key=lambda v: (chosen.count(v), v is values[0]))
        else:
            med = statistics.median(chosen)
            selected[key] = min(values, key=lambda v: (abs(v - med), values.index(v)))
    return selected


@dataclass
class WalkForwardResult:
    folds: list[Fold]
    per_fold_best: list[dict]
    per_fold_is_sharpe: list[float]
    oos_sims: list[SimResult]           # 폴드별 best 파라미터의 OOS 재생 — 유일한 OOS 접근
    oos_returns: np.ndarray             # 연결 곡선의 일일 수익률
    oos_dates: list[str]
    selected_params: dict
    # BT-G4 평탄 지대 판정 입력 — 탐색 중 이미 계산된 IS 점수만 사용 (OOS 추가 접근 없음).
    # QUANTBOT-STRAT v1.1 §S9 개정: 이웃 평탄성은 폴드별 IS 샤프의 평균으로 측정한다
    # (OOS로 재면 BT-02의 OOS-1회 규율과 충돌).
    selected_mean_is_sharpe: float
    neighbor_mean_is_sharpes: list[float]
    n_configs_tried: int                # registry 이벤트 수와 일치해야 함


def run_walkforward(
    registry: Registry,
    store: MarketDataStore,
    strategy_id: str,
    grid: dict[str, list],
    data_range: tuple[str, str],
    signal_fn: SignalFn,
    cost_model: CostModel,
    m: Methodology,
    order_unit: str = "fractional",
) -> WalkForwardResult:
    """사전등록 봉인 검증 → IS 탐색(전 시도 기록) → 폴드별 OOS 1회 → 평탄 지대 선택."""
    prereg.require_seal(registry, strategy_id, grid, data_range, folds_spec(m, order_unit))

    lo = store.index_of(data_range[0])
    hi = store.index_of(data_range[1])
    if lo >= hi:
        raise WalkForwardError(f"데이터 범위 오류: {data_range}")
    folds = [
        Fold(f.train_start + lo, f.train_end + lo, f.test_start + lo, f.test_end + lo)
        for f in make_folds(hi - lo + 1, m)
    ]
    configs = grid_configs(grid)

    per_fold_best: list[dict] = []
    per_fold_is_sharpe: list[float] = []
    is_scores_by_config: dict[str, list[float]] = {}  # 평탄 지대 판정의 원천 (BT-G4)
    tried = 0
    for k, fold in enumerate(folds):
        best_params, best_score = None, -np.inf
        for params in configs:  # IS에서만 탐색 (BT-03)
            r = simulate(
                store, fold.train_start, fold.train_end - 1,
                signal_fn, params, cost_model, m, order_unit,
            )
            score = sharpe(r.returns(), m.trading_days_per_year)
            registry.append_event(
                EVENT_CONFIG_TRIED,
                "info",
                {"strategy_id": strategy_id, "fold": k, "params": params,
                 "is_sharpe": score},
            )
            tried += 1
            is_scores_by_config.setdefault(prereg.canonical_json(params), []).append(score)
            if score > best_score:
                best_params, best_score = params, score
        per_fold_best.append(best_params)
        per_fold_is_sharpe.append(best_score)

    # 폴드별 OOS — best 파라미터로 1회 평가 (BT-03)
    oos_sims = [
        simulate(store, f.test_start, f.test_end - 1,
                 signal_fn, per_fold_best[k], cost_model, m, order_unit)
        for k, f in enumerate(folds)
    ]
    oos_returns = np.concatenate([r.returns() for r in oos_sims])
    # 연결 equity 곡선(수익률 개수 + 1)과 길이가 맞는 날짜 축
    oos_dates = [oos_sims[0].dates[0]] + [d for r in oos_sims for d in r.dates[1:]]

    selected = select_plateau_median(grid, per_fold_best)

    # BT-G4 입력 — 탐색에서 이미 나온 IS 점수의 폴드 평균. OOS 시뮬레이션은 위의
    # 폴드별 best 1회가 전부이며, 선택점·이웃을 위해 OOS를 다시 열지 않는다.
    def mean_is_sharpe_of(params: dict) -> float:
        return float(np.mean(is_scores_by_config[prereg.canonical_json(params)]))

    selected_mean_is = mean_is_sharpe_of(selected)
    neighbor_mean_is = [mean_is_sharpe_of(nb) for nb in grid_neighbors(grid, selected)]

    return WalkForwardResult(
        folds=folds,
        per_fold_best=per_fold_best,
        per_fold_is_sharpe=per_fold_is_sharpe,
        oos_sims=oos_sims,
        oos_returns=oos_returns,
        oos_dates=oos_dates,
        selected_params=selected,
        selected_mean_is_sharpe=selected_mean_is,
        neighbor_mean_is_sharpes=neighbor_mean_is,
        n_configs_tried=tried,
    )
