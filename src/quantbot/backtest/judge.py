"""판정기 (BT-G1~G7, §S8) — OOS는 전략 id별로 한 번만 열린다 (IMPL-05).

구조로 강제되는 것:
- evaluate_oos는 실행 전 registry에서 oos_opened(strategy_id) 이벤트를 검사한다.
  이미 있으면 동일 run 해시의 재현 실행(결과 재계산·검산)만 허용하고, 다른 그리드·
  게이트·시드로의 재평가는 거부한다. OOS를 다시 열려면 새 전략 id로 사전등록부터.
- n_configs_tried는 registry의 config_tried 이벤트 수 — 보고자가 적는 값이 아니다 (BT-G7).
- 판정 아티팩트·생명주기 전이는 append-only registry에 기록된다 — 결과 은폐 불가.

주: 전략 파일 해시(BT-G7의 sha256(strategy_file))는 전략 파일이 생기는 Phase 3에서
아티팩트에 추가된다. Phase 1의 정체성 앵커는 사전등록 해시다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from quantbot.backtest import prereg, walkforward
from quantbot.backtest.config import Gates, Methodology
from quantbot.backtest.costs import CostModel
from quantbot.backtest.data import MarketDataStore
from quantbot.backtest.sim import (
    SignalFn,
    cagr,
    equity_from_returns,
    mdd,
    mdd_recovery_days,
    sharpe,
    worst_month_return,
)
from quantbot.engine.registry import Registry

EVENT_OOS_OPENED = "oos_opened"
EVENT_OOS_REPRODUCED = "oos_reproduced"
ARTIFACT_JUDGEMENT = "backtest_judgement"
STATE_BACKTEST = "backtest"
STATE_PAPER = "paper"
STATE_REJECTED = "rejected"
REASON_INV04 = "INV-04"


class OosAlreadyOpenedError(ValueError):
    """OOS 재개봉 시도 — 새 전략 id로 사전등록부터 다시 (BT-02)."""


class JudgeError(ValueError):
    pass


@dataclass
class JudgeResult:
    strategy_id: str
    flags: dict[str, bool]
    metrics: dict
    selected_params: dict
    n_configs_tried: int
    artifact_sha: str
    transition: str | None      # "paper" | "rejected" | None(재현 실행)
    reproduction: bool
    reproduction_match: bool | None


def block_bootstrap_mdd_percentile(
    returns: np.ndarray,
    n_samples: int,
    block_len: int,
    seed: int,
    initial: float,
    percentile: float,
) -> float:
    """블록 부트스트랩(자기상관 보존) MDD 분포의 백분위 (§S8 b)."""
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n < block_len:
        raise JudgeError(f"수익률 {n}개 < 블록 길이 {block_len} — 부트스트랩 불가")
    rng = np.random.default_rng(seed)
    n_blocks = -(-n // block_len)  # ceil
    starts = rng.integers(0, n - block_len + 1, size=(n_samples, n_blocks))
    mdds = np.empty(n_samples)
    for k in range(n_samples):
        sample = np.concatenate([r[s : s + block_len] for s in starts[k]])[:n]
        mdds[k] = mdd(equity_from_returns(sample, initial))
    return float(np.percentile(mdds, percentile))


def stress_window_mdds(
    dates: list[str], equity: np.ndarray, windows: tuple[tuple[str, str], ...]
) -> dict[str, float]:
    """OOS 곡선과 겹치는 스트레스 창별 구간 MDD (§S8 c)."""
    out: dict[str, float] = {}
    for start, end in windows:
        idx = [i for i, d in enumerate(dates) if start <= d <= end]
        if len(idx) >= 2:
            out[f"{start}/{end}"] = mdd(equity[idx[0] : idx[-1] + 1])
    return out


def _run_sha(
    prereg_sha: str, gates: Gates, m: Methodology, cost_model: CostModel
) -> str:
    """판정에 영향을 주는 전 입력의 해시 — 재현 실행 판별 기준."""
    return prereg.sha256_hex(
        prereg.canonical_json(
            {
                "prereg_sha": prereg_sha,
                "gates": gates.as_dict(),
                "methodology": m.as_dict(),
                "costs": cost_model.as_dict(),
            }
        )
    )


def evaluate_oos(
    registry: Registry,
    store: MarketDataStore,
    strategy_id: str,
    grid: dict[str, list],
    data_range: tuple[str, str],
    signal_fn: SignalFn,
    cost_model: CostModel,
    m: Methodology,
    gates: Gates,
) -> JudgeResult:
    prereg_sha = prereg.require_seal(
        registry, strategy_id, grid, data_range, walkforward.folds_spec(m)
    )
    run_sha = _run_sha(prereg_sha, gates, m, cost_model)

    opened = [
        e for e in registry.events(EVENT_OOS_OPENED)
        if e["payload"].get("strategy_id") == strategy_id
    ]
    reproduction = False
    if opened:
        if opened[0]["payload"].get("run_sha") != run_sha:
            raise OosAlreadyOpenedError(
                f"전략 {strategy_id!r}의 OOS는 이미 열렸다 "
                f"(기존 run {opened[0]['payload'].get('run_sha', '')[:12]}…). "
                "다른 입력으로의 재평가는 거부된다 — 재탐색은 새 전략 id로 "
                "사전등록부터 다시 (BT-02, IMPL-05)."
            )
        reproduction = True  # 동일 해시 — 결과 재계산·검산만 허용
    else:
        registry.append_event(
            EVENT_OOS_OPENED,
            "audit",
            {"strategy_id": strategy_id, "run_sha": run_sha, "prereg_sha": prereg_sha},
        )

    wf = walkforward.run_walkforward(
        registry, store, strategy_id, grid, data_range, signal_fn, cost_model, m
    )

    equity = equity_from_returns(wf.oos_returns, m.initial_capital_krw)
    dates = wf.oos_dates
    oos_mdd = mdd(equity)
    oos_sharpe = sharpe(wf.oos_returns, m.trading_days_per_year)
    oos_cagr = cagr(equity, m.trading_days_per_year)
    is_sharpe_mean = float(np.mean(wf.per_fold_is_sharpe))
    p_mdd = block_bootstrap_mdd_percentile(
        wf.oos_returns,
        m.bootstrap_n_samples,
        m.bootstrap_block_len,
        m.bootstrap_seed,
        m.initial_capital_krw,
        gates.g2_mdd_percentile,
    )
    stress = stress_window_mdds(dates, equity, gates.g2_stress_windows)

    order_notionals = [x for r in wf.oos_sims for x in r.order_notionals]
    avg_order_notional = float(np.mean(order_notionals)) if order_notionals else 0.0
    total_costs = sum(r.total_costs for r in wf.oos_sims)
    years = len(wf.oos_returns) / m.trading_days_per_year
    mean_equity = float(np.mean(equity))
    cost_drag_annual = total_costs / mean_equity / years if years > 0 else 0.0

    trades = [t for r in wf.oos_sims for t in r.trades]
    wins = [t for t in trades if t.realized_pnl > 0]
    gross_win = sum(t.realized_pnl for t in wins)
    gross_loss = -sum(t.realized_pnl for t in trades if t.realized_pnl < 0)
    turnover = sum(order_notionals) / mean_equity / years / 2.0 if years > 0 else 0.0
    exposure = (
        sum(r.exposure_days for r in wf.oos_sims)
        / sum(len(r.dates) for r in wf.oos_sims)
    )

    flags = {
        "BT-G1": oos_mdd <= gates.g1_max_oos_mdd,
        "BT-G2": (
            p_mdd <= gates.g2_p95_mdd_max
            and all(v <= gates.g2_stress_mdd_max for v in stress.values())
        ),
        "BT-G3": oos_cagr > gates.g3_min_cagr and oos_sharpe >= gates.g3_min_sharpe,
        # BT-G4 — 평탄 지대는 IS 폴드 평균으로 판정 (QUANTBOT-STRAT v1.1 §S9).
        "BT-G4": (
            wf.selected_mean_is_sharpe > 0.0
            and all(
                nb >= wf.selected_mean_is_sharpe * gates.g4_plateau_min_ratio
                for nb in wf.neighbor_mean_is_sharpes
            )
        ),
        "BT-G5": oos_sharpe >= is_sharpe_mean * gates.g5_oos_is_min_ratio,
        "BT-G6": (
            avg_order_notional > 0.0
            and cost_model.min_commission_krw / avg_order_notional
            <= cost_model.slippage_rate
        ),
    }

    # BT-G7 — n_configs_tried는 registry가 센 값
    n_tried_registry = sum(
        1 for e in registry.events(walkforward.EVENT_CONFIG_TRIED)
        if e["payload"].get("strategy_id") == strategy_id
    )

    metrics = {
        "oos_mdd": oos_mdd,
        "oos_mdd_recovery_days": mdd_recovery_days(equity),
        "oos_sharpe": oos_sharpe,
        "oos_cagr_after_costs_taxes": oos_cagr,
        "is_sharpe_mean": is_sharpe_mean,
        "bootstrap_mdd_percentile": p_mdd,
        "stress_window_mdds": stress,
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else 0.0,
        "annual_turnover": turnover,
        "exposure_ratio": exposure,
        "worst_month_return": worst_month_return(dates, equity),
        "avg_order_notional": avg_order_notional,
        "cost_drag_annual": cost_drag_annual,
        "total_costs": total_costs,
        "total_taxes": sum(r.total_taxes for r in wf.oos_sims),
        "n_folds": len(wf.folds),
        "per_fold_best": wf.per_fold_best,
        "plateau_basis": "is_fold_mean",  # BT-G4 판정 근거의 출처 명시
        "selected_mean_is_sharpe": wf.selected_mean_is_sharpe,
        "neighbor_mean_is_sharpes": wf.neighbor_mean_is_sharpes,
    }

    payload = {
        "strategy_id": strategy_id,
        "prereg_sha": prereg_sha,
        "run_sha": run_sha,
        "flags": flags,
        "metrics": metrics,
        "selected_params": wf.selected_params,
        "n_configs_tried": n_tried_registry,
    }
    artifact_sha = prereg.sha256_hex(prereg.canonical_json(payload))
    registry.append_artifact(strategy_id, ARTIFACT_JUDGEMENT, artifact_sha, payload)

    transition: str | None
    reproduction_match: bool | None = None
    if reproduction:
        # 검산 — 최초 판정과 flags·metrics·선택 파라미터가 일치해야 한다
        first = registry.artifacts(strategy_id=strategy_id, kind=ARTIFACT_JUDGEMENT)[0]
        keys = ("flags", "metrics", "selected_params")
        reproduction_match = all(
            prereg.canonical_json(first["payload"][k]) == prereg.canonical_json(payload[k])
            for k in keys
        )
        registry.append_event(
            EVENT_OOS_REPRODUCED,
            "audit" if reproduction_match else "critical",
            {"strategy_id": strategy_id, "run_sha": run_sha,
             "match": reproduction_match},
        )
        transition = None  # 재현 실행은 생명주기를 움직이지 않는다
    elif all(flags.values()):
        transition = STATE_PAPER
        registry.append_strategy_transition(
            strategy_id, STATE_BACKTEST, STATE_PAPER,
            f"BT-G1..G6 전부 충족 (LC-G2) · artifact {artifact_sha[:12]}",
        )
    else:
        transition = STATE_REJECTED
        if not flags["BT-G1"]:
            reason = REASON_INV04  # BT-G7 — MDD 예산 위반은 INV-04로 기록
        else:
            failed = ",".join(k for k, v in flags.items() if not v)
            reason = failed
        registry.append_strategy_transition(
            strategy_id, STATE_BACKTEST, STATE_REJECTED,
            f"{reason} · artifact {artifact_sha[:12]}",
        )

    return JudgeResult(
        strategy_id=strategy_id,
        flags=flags,
        metrics=metrics,
        selected_params=wf.selected_params,
        n_configs_tried=n_tried_registry,
        artifact_sha=artifact_sha,
        transition=transition,
        reproduction=reproduction,
        reproduction_match=reproduction_match,
    )
