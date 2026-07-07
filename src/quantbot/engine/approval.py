"""자동 승인 파이프라인 (GATE-03) — LC-G1 정적 검사(Phase 3) + 4단 승인(Phase 4).

신뢰 모델: LLM(또는 사람)의 판단을 믿는 게 아니라, 산출물이 불변식을 만족하는지
기계적으로 검증한다. 하나라도 실패하면 자동 거부 + 에스컬레이션 — 위반을
"완화"하거나 경고로 낮추는 코드 경로는 없다 (GATE-01).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from quantbot.engine import portfolio
from quantbot.engine.invariants import (
    Invariants,
    VERDICT_ELIGIBLE,
    broad_etf_cap_eligible,
    leverage_verdict,
)
from quantbot.engine.registry import Registry
from quantbot.strategy.schema import StrategyFile

EVENT_PAPER_PASSED = "paper_gate_passed"   # LC-G3 충족 기록 (INV-08의 근거)
PCT = 100.0  # 백분율 ↔ 비율 변환 (단위 변환 상수 — 파라미터 아님)


# ── 1단 · LC-G1: 스키마 + 정적 불변식 (Phase 3 DoD) ──────────────────────


def static_invariant_check(
    strategy: StrategyFile,
    inv: Invariants,
    universe_symbols: Mapping[str, list[str]],
    whitelist: Mapping[str, set[str]] | None = None,
    stock_master: Mapping[str, tuple[str, str | None]] | None = None,
    broad_etf_symbols: frozenset[str] = frozenset(),
) -> list[str]:
    """위반 목록을 반환한다 — 빈 리스트 = 통과. 검사는 전부 AND (GATE-01).

    universe_symbols: sleeve → 실제 심볼 목록 (유니버스 파일/스크리너 결과).
    whitelist: sleeve → 허용 심볼 (INV-03). None이면 이 항목은 '검증 불가' 위반.
    stock_master: symbol → (securityType, leverageFactor) — INV-11/01a 입력.
        exclude_leveraged_etf=true인데 None이면 '검증 불가' 위반 (fail-closed).
    broad_etf_symbols (INV-01a): 사람 큐레이션 분산형 ETF 목록. sleeve 전 종목이
        이 목록에 있고 기계 검증(ETF 계열 ∧ leverageFactor=1.0)을 통과할 때만
        해당 sleeve에 ETF 캡을 적용한다 — 혼합·미검증은 기존 12% (좁은 예외).
    """
    violations: list[str] = []

    # INV-05 — 주기 하한
    if strategy.cadence.rebalance != "weekly" or inv.rebalance.min_interval_days > 7:
        violations.append(
            f"INV-05: 리밸런싱 주기 위반 — {strategy.cadence.rebalance!r}, "
            f"하한 {inv.rebalance.min_interval_days}일"
        )

    # INV-01/01a — 최악 케이스 종목 비중 = sleeve_alloc / n(진입 보유 수) ≤ 캡
    cap_stock = inv.position.max_weight_pct / PCT
    cap_etf = inv.position.max_weight_pct_broad_etf / PCT
    n_entry = int(strategy.entry_exit.entry.params.get("n", 0))
    if n_entry < 1:
        violations.append("INV-01: entry.params.n ≥ 1 필요")
    else:
        for sleeve, alloc in strategy.sizing.sleeves.items():
            symbols = list(universe_symbols.get(sleeve, []))
            etf_sleeve = bool(symbols) and broad_etf_symbols and (
                set(symbols) <= set(broad_etf_symbols)
            )
            if etf_sleeve:
                # INV-01a 이중 검증 — 기계 검증 실패는 캡 강등이 아니라 위반
                # (50%로 사이징된 전략이 12%로 잘리면 다른 전략이 되므로)
                if stock_master is None:
                    violations.append(
                        f"INV-01a: {sleeve} 종목 마스터 미제공 — 검증 불가 (fail-closed)"
                    )
                    etf_sleeve = False
                else:
                    for s in sorted(set(symbols)):
                        sec_type, factor = stock_master.get(s, ("", None))
                        if not broad_etf_cap_eligible(
                            s, broad_etf_symbols, sec_type, factor
                        ):
                            violations.append(
                                f"INV-01a: {s} 기계 검증 실패 "
                                f"(securityType={sec_type!r}, leverageFactor={factor!r})"
                            )
                            etf_sleeve = False
            cap = cap_etf if etf_sleeve else cap_stock
            inv_id = "INV-01a" if etf_sleeve else "INV-01"
            worst = alloc / n_entry
            if worst > cap + 1e-12:
                violations.append(
                    f"{inv_id}: {sleeve} 최악 비중 {worst:.4f} > 캡 {cap:.4f} "
                    f"(alloc {alloc} / n {n_entry})"
                )

    # INV-03 — 화이트리스트 유니버스
    if inv.universe.whitelist_only:
        if whitelist is None:
            violations.append("INV-03: 화이트리스트 미제공 — 검증 불가 (fail-closed)")
        else:
            for sleeve, symbols in universe_symbols.items():
                allowed = whitelist.get(sleeve, set())
                outside = sorted(set(symbols) - allowed)
                if outside:
                    violations.append(f"INV-03: {sleeve} 화이트리스트 밖: {outside}")

    # INV-11 — 레버리지·인버스 배제 (기계 검증이 주 방어)
    if inv.universe.exclude_leveraged_etf:
        if stock_master is None:
            violations.append("INV-11: 종목 마스터 미제공 — 검증 불가 (fail-closed)")
        else:
            for sleeve, symbols in universe_symbols.items():
                for s in sorted(set(symbols)):
                    if s not in stock_master:
                        violations.append(f"INV-11: {s} 종목 정보 없음 — 판정 불가")
                        continue
                    sec_type, factor = stock_master[s]
                    verdict = leverage_verdict(sec_type, factor)
                    if verdict != VERDICT_ELIGIBLE:
                        violations.append(
                            f"INV-11: {s} {verdict} "
                            f"(securityType={sec_type}, leverageFactor={factor})"
                        )
    return violations


# ── 2~4단 · 백테스트/페이퍼 게이트 + 전환 충격 (Phase 4, GATE-03 표) ─────


@dataclass(frozen=True)
class ApprovalResult:
    auto_approved: bool
    reasons: tuple[str, ...] = ()                 # 거부·에스컬레이션 사유
    staged_plan: tuple[dict, ...] | None = None   # 회전율 초과 시 단계 전환 제안


def approve_switch(
    strategy: StrategyFile,
    registry: Registry,
    inv: Invariants,
    universe_symbols: Mapping[str, list[str]],
    whitelist: Mapping[str, set[str]] | None,
    stock_master: Mapping[str, tuple[str, str | None]] | None,
    current_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
) -> ApprovalResult:
    """GATE-03 4단 — 전부 통과 = 자동 승인, 하나라도 실패 = 에스컬레이션."""
    reasons: list[str] = []

    # 1단: 스키마·정적 불변식
    reasons += static_invariant_check(
        strategy, inv, universe_symbols, whitelist, stock_master
    )

    # 2단: 백테스트 게이트 — registry에 backtest→paper 전이(LC-G2)가 있어야 한다
    sid = strategy.meta.id
    transitions = registry.transitions(sid)
    if not any(
        t["from_state"] == "backtest" and t["to_state"] == "paper" for t in transitions
    ):
        reasons.append(f"LC-G2: {sid} 백테스트 게이트 통과 기록 없음")

    # 3단: 페이퍼 게이트 (INV-08) — 미통과는 무조건 에스컬레이션
    if inv.lifecycle.auto_approve_requires_paper:
        passed = any(
            e["payload"].get("strategy_id") == sid
            for e in registry.events(EVENT_PAPER_PASSED)
        )
        if not passed:
            reasons.append(f"INV-08: {sid} 페이퍼 1개월 통과 기록 없음")

    # 4단: 전환 충격 — 회전율 ≤ INV-06
    turnover = portfolio.turnover(current_weights, target_weights)
    max_auto = inv.turnover.auto_approve_max_pct / PCT
    staged = None
    if turnover > max_auto:
        staged = tuple(
            portfolio.staged_transition_plan(current_weights, target_weights, max_auto)
        )
        reasons.append(
            f"INV-06: 전환 회전율 {turnover:.4f} > 자동 승인 상한 {max_auto:.4f} — "
            f"단계 전환 계획 {len(staged)}주 제안"
        )

    return ApprovalResult(
        auto_approved=not reasons,
        reasons=tuple(reasons),
        staged_plan=staged,
    )
