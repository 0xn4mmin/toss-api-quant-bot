"""백테스트 설정 로더 — 방법론·게이트 상수는 전부 설정 파일 주입 (IMPL-07 Phase 1).

코드에는 수치가 없다. config/backtest.yaml(방법론·비용)과
config/backtest_gates.yaml(BT-G 게이트 상수 — 사람이 git 커밋으로만 변경)을 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quantbot import _yaml


class BacktestConfigError(ValueError):
    """설정 파일 누락·형식 오류 — 부분 로드 없음."""


@dataclass(frozen=True)
class Methodology:
    train_days: int              # BT-03 IS 길이 (거래일)
    test_days: int               # BT-03 OOS 길이
    step_days: int               # rolling 보폭
    rebalance_every_n_days: int
    trading_days_per_year: int
    initial_capital_krw: float
    bootstrap_n_samples: int     # §S8 (b)
    bootstrap_block_len: int
    bootstrap_seed: int

    def as_dict(self) -> dict:
        return {
            "train_days": self.train_days,
            "test_days": self.test_days,
            "step_days": self.step_days,
            "rebalance_every_n_days": self.rebalance_every_n_days,
            "trading_days_per_year": self.trading_days_per_year,
            "initial_capital_krw": self.initial_capital_krw,
            "bootstrap_n_samples": self.bootstrap_n_samples,
            "bootstrap_block_len": self.bootstrap_block_len,
            "bootstrap_seed": self.bootstrap_seed,
        }


@dataclass(frozen=True)
class Gates:
    """BT-G1..G6 게이트 상수 (§S9) — 백테스트가 정하는 값이 아니다."""

    g1_max_oos_mdd: float
    g2_mdd_percentile: float
    g2_p95_mdd_max: float
    g2_stress_mdd_max: float
    g2_stress_windows: tuple[tuple[str, str], ...]
    g3_min_cagr: float
    g3_min_sharpe: float
    g4_plateau_min_ratio: float
    g5_oos_is_min_ratio: float

    def as_dict(self) -> dict:
        return {
            "g1_max_oos_mdd": self.g1_max_oos_mdd,
            "g2_mdd_percentile": self.g2_mdd_percentile,
            "g2_p95_mdd_max": self.g2_p95_mdd_max,
            "g2_stress_mdd_max": self.g2_stress_mdd_max,
            "g2_stress_windows": [list(w) for w in self.g2_stress_windows],
            "g3_min_cagr": self.g3_min_cagr,
            "g3_min_sharpe": self.g3_min_sharpe,
            "g4_plateau_min_ratio": self.g4_plateau_min_ratio,
            "g5_oos_is_min_ratio": self.g5_oos_is_min_ratio,
        }


def _require(data: dict, key: str, types: type | tuple) -> object:
    val = data.get(key)
    if isinstance(val, bool) or not isinstance(val, types):
        raise BacktestConfigError(f"{key}: 형식 오류 또는 누락: {val!r}")
    return val


def load_methodology(path: str | Path) -> tuple[Methodology, dict]:
    """config/backtest.yaml → (Methodology, costs dict). costs는 costs.CostModel이 소화."""
    p = Path(path)
    if not p.is_file():
        raise BacktestConfigError(f"백테스트 설정 파일이 없다: {p}")
    data = _yaml.load_file(str(p))
    m = data.get("methodology")
    if not isinstance(m, dict):
        raise BacktestConfigError("methodology 섹션 누락")
    boot = m.get("bootstrap")
    if not isinstance(boot, dict):
        raise BacktestConfigError("methodology.bootstrap 섹션 누락")
    costs = data.get("costs")
    if not isinstance(costs, dict):
        raise BacktestConfigError("costs 섹션 누락")
    meth = Methodology(
        train_days=_require(m, "train_days", int),
        test_days=_require(m, "test_days", int),
        step_days=_require(m, "step_days", int),
        rebalance_every_n_days=_require(m, "rebalance_every_n_days", int),
        trading_days_per_year=_require(m, "trading_days_per_year", int),
        initial_capital_krw=float(_require(m, "initial_capital_krw", (int, float))),
        bootstrap_n_samples=_require(boot, "n_samples", int),
        bootstrap_block_len=_require(boot, "block_len", int),
        bootstrap_seed=_require(boot, "seed", int),
    )
    return meth, costs


def _parse_window(raw: object) -> tuple[str, str]:
    if not isinstance(raw, str) or raw.count("/") != 1:
        raise BacktestConfigError(f"스트레스 창 형식은 'YYYY-MM-DD/YYYY-MM-DD': {raw!r}")
    start, end = raw.split("/")
    if not (start < end):
        raise BacktestConfigError(f"스트레스 창 시작 ≥ 끝: {raw!r}")
    return (start, end)


def load_gates(path: str | Path) -> Gates:
    p = Path(path)
    if not p.is_file():
        raise BacktestConfigError(f"게이트 상수 파일이 없다: {p}")
    data = _yaml.load_file(str(p))
    windows = data.get("g2_stress_windows")
    if not isinstance(windows, list) or not windows:
        raise BacktestConfigError("g2_stress_windows: 최소 1개 필요 (§S7 스트레스 창 의무)")
    return Gates(
        g1_max_oos_mdd=float(_require(data, "g1_max_oos_mdd", (int, float))),
        g2_mdd_percentile=float(_require(data, "g2_mdd_percentile", (int, float))),
        g2_p95_mdd_max=float(_require(data, "g2_p95_mdd_max", (int, float))),
        g2_stress_mdd_max=float(_require(data, "g2_stress_mdd_max", (int, float))),
        g2_stress_windows=tuple(_parse_window(w) for w in windows),
        g3_min_cagr=float(_require(data, "g3_min_cagr", (int, float))),
        g3_min_sharpe=float(_require(data, "g3_min_sharpe", (int, float))),
        g4_plateau_min_ratio=float(_require(data, "g4_plateau_min_ratio", (int, float))),
        g5_oos_is_min_ratio=float(_require(data, "g5_oos_is_min_ratio", (int, float))),
    )


def load_grid(path: str | Path) -> dict[str, list]:
    """사전등록 그리드 파일 → {param: [후보값,...]}. 이 dict가 봉인 대상이다 (BT-02)."""
    p = Path(path)
    if not p.is_file():
        raise BacktestConfigError(f"그리드 파일이 없다: {p}")
    data = _yaml.load_file(str(p))
    grid: dict[str, list] = {}
    for key, val in data.items():
        if not isinstance(val, list) or not val:
            raise BacktestConfigError(f"그리드 {key}: 비어 있지 않은 리스트여야 한다: {val!r}")
        grid[key] = val
    if not grid:
        raise BacktestConfigError("그리드가 비어 있다")
    return grid
