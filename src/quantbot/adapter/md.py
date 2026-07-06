"""md.* — 시장 데이터 조회 표면 (ARCH-06: quote get/batch/chart/flows/orderbook/
warnings/commission/limits). 전 함수가 contracts.call 3단(proc → JSON → 계약)을 통과한다.

BT-D2 수정주가 검증 루틴 포함 — 분할 이벤트 전후의 chart로 종가가 조정돼 있는지
판정하는 리포트를 만든다 (미조정 종가로 계산한 모멘텀은 쓰레기다, §S2).
"""

from __future__ import annotations

from quantbot.adapter import contracts
from quantbot.adapter.contracts import (
    Chart,
    Commission,
    Flows,
    Orderbook,
    Quote,
    QuoteBatch,
    TradeLimits,
    WarningFlags,
    call,
)
from quantbot.adapter.proc import TossctlRunner


def quote_get(runner: TossctlRunner, symbol: str) -> Quote:
    return call(runner, ["quote", "get", symbol], Quote)


def quote_batch(runner: TossctlRunner, symbols: list[str]) -> QuoteBatch:
    return call(runner, ["quote", "batch", *symbols], QuoteBatch)


def chart(runner: TossctlRunner, symbol: str, period: str, n: int) -> Chart:
    return call(
        runner, ["quote", "chart", symbol, "--period", period, "--count", str(n)], Chart
    )


def flows(runner: TossctlRunner, symbol: str) -> Flows:
    return call(runner, ["quote", "flows", symbol], Flows)


def orderbook(runner: TossctlRunner, symbol: str) -> Orderbook:
    return call(runner, ["quote", "orderbook", symbol], Orderbook)


def warnings(runner: TossctlRunner, symbol: str) -> WarningFlags:
    return call(runner, ["quote", "warnings", symbol], WarningFlags)


def commission(runner: TossctlRunner) -> Commission:
    return call(runner, ["quote", "commission"], Commission)


def limits(runner: TossctlRunner, symbol: str) -> TradeLimits:
    return call(runner, ["quote", "limits", symbol], TradeLimits)


# ═══ BT-D2 — 수정주가 검증 (Phase 2 DoD) ═══════════════════════════════


def adjusted_price_report(
    chart_data: contracts.Chart,
    split_date: str,
    split_ratio: float,
    daily_move_tolerance: float,
) -> dict:
    """분할 이벤트(date, ratio)를 아는 종목의 chart로 수정주가 여부를 판정한다.

    미조정 종가라면 분할일에 가격이 약 1/ratio 로 점프하고, 조정 종가라면
    통상 일변동 범위에 머문다. 판정 불가 구간은 'inconclusive' — 미해결 시
    백테스트 자체가 무효다 (BT-D2).
    """
    if split_ratio <= 1.0:
        raise ValueError(f"분할 비율은 1 초과여야 한다: {split_ratio}")
    dates = [b.date for b in chart_data.bars]
    if split_date not in dates:
        raise ValueError(f"chart에 분할일 {split_date}가 없다")
    i = dates.index(split_date)
    if i == 0:
        raise ValueError("분할일 이전 봉이 없다 — 판정 불가")
    pre, post = chart_data.bars[i - 1].close, chart_data.bars[i].close
    observed = post / pre
    unadjusted_expected = 1.0 / split_ratio
    if abs(observed - unadjusted_expected) <= unadjusted_expected * daily_move_tolerance:
        verdict = "unadjusted"       # 분할 점프가 그대로 보인다 → 조정 계수 구축 필요
    elif abs(observed - 1.0) <= daily_move_tolerance:
        verdict = "adjusted"         # 연속적 — 분할이 흡수돼 있다
    else:
        verdict = "inconclusive"     # 백테스트 무효 사유 (BT-D2)
    return {
        "symbol": chart_data.symbol,
        "split_date": split_date,
        "split_ratio": split_ratio,
        "pre_close": pre,
        "post_close": post,
        "observed_move": observed,
        "unadjusted_expected_move": unadjusted_expected,
        "verdict": verdict,
    }
