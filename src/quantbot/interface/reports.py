"""아침 보고서 (ARCH-08) — 무엇을 왜 샀/팔았는지, 근거와 함께.

의무 요소: 시그널 근거(값·순위·노출), 체결·비용, 미검증 표기(RISK-04 —
게이트를 통과하지 않은 시그널은 문서·보고서에 '미검증' 표기 의무), hold 상태.
데이터는 엔진 조회 결과를 주입받는 순수 포매터다.
"""

from __future__ import annotations

from typing import Mapping, Sequence


def morning_report(
    *,
    date: str,
    strategy_id: str,
    lifecycle_state: str,
    exposure: float,
    signal_notes: Sequence[str],
    fills: Sequence[Mapping],
    costs_krw: float,
    pnl_day_krw: float,
    equity_krw: float,
    holds: Sequence[str] = (),
    unverified: bool = True,
) -> str:
    lines = [
        f"■ 아침 보고서 {date}",
        f"전략 {strategy_id} · 상태 {lifecycle_state}"
        + (" · ⚠ 미검증 (게이트 미통과 — RISK-04)" if unverified else ""),
        f"평가액 {equity_krw:,.0f}원 · 일손익 {pnl_day_krw:+,.0f}원 · 노출 {exposure:.0%}",
    ]
    if holds:
        lines.append(f"⛔ fail-safe hold: {', '.join(holds)} — /resume (Tier-2)")
    lines.append("— 시그널 근거 —")
    lines += [f"  {n}" for n in signal_notes] or ["  (없음)"]
    lines.append("— 체결 —")
    if fills:
        for f in fills:
            lines.append(
                f"  {f['side']} {f['symbol']} {f['qty']}주 @ {f['exec_price']:,.2f} "
                f"(수수료 {f.get('commission', 0):,.2f})"
            )
    else:
        lines.append("  (없음)")
    lines.append(f"— 비용 합계 {costs_krw:,.2f}원 —")
    return "\n".join(lines)
