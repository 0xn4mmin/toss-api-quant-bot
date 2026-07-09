"""연구용 페이퍼 운용 (2026-07-08 소유자 위임 결정) — 순방향 검증 체제.

배경: 백테스트 5회 판정 결과 주식·자산군 롱온리로는 MDD 예산(INV-04)을 이
OOS 구간에서 충족하지 못했고, 같은 과거를 반복 조회하는 것은 메타 과적합이다.
미래 데이터는 오염되지 않는 유일한 OOS — rejected 전략(dual-momentum.v3)을
승격 경로 없이 페이퍼로 상시 가동해 순방향 실적을 쌓는다.

안전 성질 (구조로 보장):
- 자동 승격 불가: LC-G2(backtest→paper 전이)·INV-08(paper_gate_passed) 기록이
  없으므로 approve_switch가 영구 거부한다 — 이 모듈은 생명주기를 만지지 않는다.
- 실주문 불가: live 분기 부재(Phase 7) + live_trading=false + 페이퍼 게이트.
- 재시작 안전: 포트폴리오는 registry의 페이퍼 체결 로그를 재생해 복원한다
  (RISK-06 — 상태는 디스크의 레지스트리에서 복원).
- 파라미터 추적성: 시그널 파라미터는 사람이 적는 게 아니라 판정 아티팩트의
  selected_params에서 읽는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Mapping

import numpy as np

from quantbot.engine import caps as caps_mod
from quantbot.engine.gate import Gate, ORDER_STATUS_PAPER_FILLED, PaperPortfolio
from quantbot.engine.invariants import Invariants
from quantbot.engine.registry import Registry
from quantbot.engine.scheduler import CycleResult, run_rebalance_cycle
from quantbot.strategy.schema import StrategyFile
from quantbot.strategy.slots import trend_score
from quantbot.strategy.slots.dual_momentum import dual_momentum_select
from quantbot.strategy.slots.pipeline import basket_vol_scalar, cap_clip_redistribute

EVENT_PAPER_SESSION = "paper_session_started"
EVENT_PAPER_CYCLE = "paper_cycle"
ARTIFACT_JUDGEMENT = "backtest_judgement"  # judge와 동일 상수 (계층상 재정의)


class PaperOpsError(ValueError):
    pass


def load_selected_params(registry: Registry, strategy_id: str) -> dict:
    """판정 아티팩트의 selected_params — 페이퍼 파라미터의 유일한 출처."""
    artifacts = registry.artifacts(strategy_id=strategy_id, kind=ARTIFACT_JUDGEMENT)
    if not artifacts:
        raise PaperOpsError(
            f"{strategy_id}: 판정 아티팩트가 없다 — 백테스트 판정 없이 페이퍼 없음"
        )
    return dict(artifacts[0]["payload"]["selected_params"])


def reset_session(
    registry: Registry, strategy_id: str, initial_cash_krw: float, reason: str
) -> None:
    """새 세션 이벤트 append — 이전 장부는 감사 기록으로 남고 재생에서 제외된다.

    registry는 append-only라 오염된 체결을 지울 수 없다(지워서도 안 된다) —
    대신 최신 세션 시각 이후의 체결만 재생하는 방식으로 장부를 새로 연다.
    """
    registry.append_event(EVENT_PAPER_SESSION, "audit", {
        "strategy_id": strategy_id,
        "initial_cash_krw": initial_cash_krw,
        "mode": "research",
        "reset_reason": reason,
    })


def start_or_resume_session(
    registry: Registry, strategy_id: str, initial_cash_krw: float
) -> PaperPortfolio:
    """세션 시작(최초 1회 기록) 또는 **최신 세션 이후** 체결 재생으로 복원."""
    sessions = [
        e for e in registry.events(EVENT_PAPER_SESSION)
        if e["payload"].get("strategy_id") == strategy_id
    ]
    if not sessions:
        registry.append_event(EVENT_PAPER_SESSION, "audit", {
            "strategy_id": strategy_id,
            "initial_cash_krw": initial_cash_krw,
            "mode": "research",  # 승격 경로 없음 — 순방향 검증 전용
        })
        return PaperPortfolio(cash=initial_cash_krw)
    latest = sessions[-1]  # 리셋 지원 — 마지막 세션이 현재 장부
    cash = float(latest["payload"]["initial_cash_krw"])
    session_started_at = latest["created_at"]
    p = PaperPortfolio(cash=cash)
    for row in registry.rows("orders"):
        # (id, intent_hash, status, payload, created_at)
        if row[2] != ORDER_STATUS_PAPER_FILLED:
            continue
        if row[4] < session_started_at:
            continue  # 이전 세션의 체결 — 감사 기록일 뿐 현재 장부가 아니다
        fill = json.loads(row[3])
        qty, px = float(fill["qty"]), float(fill["exec_price"])
        fee, tax = float(fill.get("commission", 0.0)), float(fill.get("tax", 0.0))
        s = fill["symbol"]
        if fill["side"] == "BUY":
            p.cash -= qty * px + fee
            prev = p.qty.get(s, 0.0)
            p.avg_cost[s] = (p.avg_cost.get(s, 0.0) * prev + px * qty) / (prev + qty)
            p.qty[s] = prev + qty
        else:
            p.cash += qty * px - fee - tax
            p.qty[s] = p.qty.get(s, 0.0) - qty
            if p.qty[s] <= 1e-12:
                p.qty.pop(s, None)
                p.avg_cost.pop(s, None)
    return p


def last_cycle_payload(registry: Registry, strategy_id: str) -> dict | None:
    cycles = [
        e for e in registry.events(EVENT_PAPER_CYCLE)
        if e["payload"].get("strategy_id") == strategy_id
    ]
    return cycles[-1]["payload"] if cycles else None


def compute_dual_momentum_targets(
    *,
    closes: Mapping[str, np.ndarray],
    holdings: list[str],
    selected_params: Mapping[str, object],
    strategy: StrategyFile,
    trading_days_per_week: int,
    trading_days_per_year: int,
    selection_due: bool,
    last_scalar: float | None,
    cap: float,
) -> tuple[dict[str, float], list[str], float]:
    """(목표 비중, 선택, vol 스칼라) — 백테스트 빌더와 같은 규칙, 상태는 registry.

    selection_due=True면 히스테리시스 재선정, 아니면 보유 유지 + 절대 모멘텀
    음전 즉시 이탈(방어는 주기를 기다리지 않는다). 스칼라 밴드도 동일 적용.
    """
    lookback = int(selected_params["lookback_wk"]) * trading_days_per_week
    skip = int(selected_params.get("skip_wk", 0)) * trading_days_per_week
    top_n = int(selected_params["top_n"])
    exit_buffer = float(strategy.entry_exit.exit.params.get("exit_buffer", 1.0))

    if selection_due:
        selection = dual_momentum_select(
            closes, lookback=lookback, top_n=top_n, skip=skip,
            holdings=holdings, exit_buffer=exit_buffer,
        )
    else:
        mom = trend_score.momentum(closes, lookback, skip)
        selection = [s for s in holdings if mom.get(s, 0.0) > 0.0]

    if not selection:
        return {}, [], 1.0

    scalar = 1.0
    if strategy.sizing.vol_target_annual is not None:
        raw = basket_vol_scalar(
            {s: closes[s] for s in selection},
            (strategy.sizing.vol_target_annual,
             strategy.sizing.vol_lookback_days, trading_days_per_year),
        )
        band = strategy.sizing.vol_scalar_band
        if band is not None and last_scalar is not None and abs(raw - last_scalar) <= band:
            scalar = last_scalar
        else:
            scalar = raw
    weights = cap_clip_redistribute(
        {s: scalar / top_n for s in selection}, cap
    )
    return weights, selection, scalar


@dataclass
class PaperCycleOutcome:
    nav_krw: float
    targets: dict[str, float]
    selection: list[str]
    scalar: float
    cycle: CycleResult


def run_paper_cycle(
    *,
    registry: Registry,
    inv: Invariants,
    caps_state: caps_mod.CapsState,
    gate: Gate,
    strategy: StrategyFile,
    strategy_id: str,
    portfolio: PaperPortfolio,
    closes: Mapping[str, np.ndarray],
    prices_krw: Mapping[str, float],
    broad_etf_symbols: frozenset[str],
    selected_params: Mapping[str, object],
    trading_days_per_week: int,
    trading_days_per_year: int,
    now_iso: str,
    month_key: str,
) -> PaperCycleOutcome:
    """주간 페이퍼 사이클 — 선택(월간)·방어(주간)·집행(caps→gate)·기록."""
    last = last_cycle_payload(registry, strategy_id)
    selection_due = last is None or last.get("month_key") != month_key
    last_scalar = None if last is None else last.get("scalar")

    # INV-01/01a — 전 자산이 검증된 분산형 ETF 집합 안일 때만 ETF 캡
    if broad_etf_symbols and set(closes) <= set(broad_etf_symbols):
        cap = inv.position.max_weight_pct_broad_etf / 100.0
    else:
        cap = inv.position.max_weight_pct / 100.0

    holdings = sorted(portfolio.qty)
    targets, selection, scalar = compute_dual_momentum_targets(
        closes=closes, holdings=holdings, selected_params=selected_params,
        strategy=strategy,
        trading_days_per_week=trading_days_per_week,
        trading_days_per_year=trading_days_per_year,
        selection_due=selection_due, last_scalar=last_scalar,
        cap=cap,
    )

    equity = portfolio.equity(prices_krw)
    position_values = portfolio.position_values(prices_krw)
    current_weights = {s: v / equity for s, v in position_values.items()}
    caps_state.start_day(equity)
    caps_state.update_equity(equity, inv)

    cycle = run_rebalance_cycle(
        inv=inv, caps_state=caps_state, gate=gate,
        target_weights=targets, current_weights=current_weights,
        band=strategy.sizing.no_trade_band,
        equity_krw=equity, cash_krw=portfolio.cash,
        position_value_krw=position_values, prices_krw=prices_krw,
        broad_etf_symbols=broad_etf_symbols,
    )
    nav = portfolio.equity(prices_krw)
    registry.append_event(EVENT_PAPER_CYCLE, "info", {
        "strategy_id": strategy_id, "at": now_iso, "month_key": month_key,
        "selection_due": selection_due, "selection": selection,
        "scalar": scalar, "targets": targets,
        "nav_krw": nav, "cash_krw": portfolio.cash,
        "fills": len(cycle.fills), "rejected": len(cycle.rejected),
    })
    return PaperCycleOutcome(
        nav_krw=nav, targets=targets, selection=selection,
        scalar=scalar, cycle=cycle,
    )
