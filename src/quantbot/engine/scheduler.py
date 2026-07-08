"""주간 리밸런싱 오케스트레이션 (IMPL-04) — 실행창 판정 + 사이클 집행.

사이클의 유일한 경로: 목표 비중 → no-trade band → 주문 의도 → caps.check →
gate.preview → gate.execute. 이 순서를 건너뛰는 코드가 없도록 사이클이 한
함수다 — 인터페이스·전략이 무엇을 요청하든 이 파이프를 지난다 (ARCH-03).

실행창: 전략 파일의 "HH:MM-HH:MM TZ" 선언을 zoneinfo로 해석한다. 자정을
넘는 창(예: 23:00-00:30)을 지원하고, 같은 (주, 창)에서 두 번 돌지 않는다
(INV-05는 불변식 검사, 이것은 운영 루프의 중복 방지).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Mapping
from zoneinfo import ZoneInfo

from quantbot.engine import caps as caps_mod
from quantbot.engine.gate import Gate
from quantbot.engine.invariants import Invariants
from quantbot.engine.portfolio import no_trade_band_orders

_TZ_ALIASES = {"KST": "Asia/Seoul", "EST": "America/New_York", "ET": "America/New_York"}


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionWindow:
    start: dtime
    end: dtime
    tz: ZoneInfo

    @classmethod
    def parse(cls, spec: str) -> "ExecutionWindow":
        """"23:00-00:30 KST" → 창. 자정 넘김 허용."""
        try:
            span, tz_name = spec.strip().rsplit(" ", 1)
            start_s, end_s = span.replace("–", "-").split("-")
            tz = ZoneInfo(_TZ_ALIASES.get(tz_name, tz_name))
            sh, sm = map(int, start_s.split(":"))
            eh, em = map(int, end_s.split(":"))
            return cls(dtime(sh, sm), dtime(eh, em), tz)
        except (ValueError, KeyError) as e:
            raise ScheduleError(f"실행창 형식 오류 {spec!r} — 'HH:MM-HH:MM TZ'") from e

    def contains(self, now_utc: datetime) -> bool:
        local = now_utc.astimezone(self.tz).time()
        if self.start <= self.end:
            return self.start <= local <= self.end
        return local >= self.start or local <= self.end  # 자정 넘김


def week_key(now_utc: datetime, window: ExecutionWindow) -> str:
    """ISO (연, 주차) — 같은 주에 같은 창을 두 번 돌지 않기 위한 키."""
    local = now_utc.astimezone(window.tz)
    iso = local.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def is_rebalance_due(
    now_utc: datetime, window: ExecutionWindow, last_done_week: str | None
) -> bool:
    return window.contains(now_utc) and week_key(now_utc, window) != last_done_week


@dataclass
class CycleResult:
    cleared: int
    rejected: tuple[tuple[caps_mod.OrderIntent, str], ...]
    fills: tuple[dict, ...]


def run_rebalance_cycle(
    *,
    inv: Invariants,
    caps_state: caps_mod.CapsState,
    gate: Gate,
    target_weights: Mapping[str, float],
    current_weights: Mapping[str, float],
    band: float,
    equity_krw: float,
    cash_krw: float,
    position_value_krw: Mapping[str, float],
    prices_krw: Mapping[str, float],
    broad_etf_symbols: frozenset[str] = frozenset(),  # INV-01a — 이중 검증 완료 집합
) -> CycleResult:
    """한 번의 리밸런싱 — 매도 먼저(현금 확보), 그다음 매수. 전 주문이 게이트 경유.

    INV-10 주문 분할: 변화 금액이 1회 상한을 넘으면 상한 이하 조각으로 나눈다 —
    상한의 목적(단일 주문 폭주·착오 캡)은 주문 단위 규제라 분할은 완화가 아니고,
    조각 수는 INV-09(일일 횟수)가 자연 상한이다.
    """
    orders = no_trade_band_orders(current_weights, target_weights, band)
    max_per_order = inv.orders.per_order_max_amount_krw
    intents: list[caps_mod.OrderIntent] = []
    for symbol in sorted(orders):
        target_v = orders[symbol] * equity_krw
        current_v = position_value_krw.get(symbol, 0.0)
        delta = target_v - current_v
        price = prices_krw.get(symbol)
        if price is None or price <= 0:
            raise ScheduleError(f"{symbol}: 시세 없음 — 사이클 중단 (fail-closed)")
        amount = abs(delta)
        if amount <= 0:
            continue
        n_chunks = max(int(-(-amount // max_per_order)), 1)  # ceil
        per_chunk = amount / n_chunks
        cm = gate.costs
        for _ in range(n_chunks):
            if delta < 0:
                intents.append(caps_mod.OrderIntent(
                    symbol, "SELL", quantity=per_chunk / price, est_price_krw=price,
                ))
            else:
                # 수수료 여유 선차감 — 조각 총비용(체결+수수료)이 조각 예산을
                # 넘지 않게 (레버리지 0의 현금 산수, 시뮬과 같은 비용 모델)
                buy_amount = max(
                    (per_chunk - cm.min_commission_krw) / (1.0 + cm.commission_rate),
                    0.0,
                )
                if buy_amount <= 0:
                    continue
                intents.append(caps_mod.OrderIntent(
                    symbol, "BUY", amount_krw=buy_amount, est_price_krw=price,
                ))
    intents.sort(key=lambda i: (i.side != "SELL", i.symbol))  # 매도 먼저

    decision = caps_mod.check(
        intents, inv, caps_state,
        equity_krw=equity_krw, cash_krw=cash_krw,
        position_value_krw=position_value_krw,
        broad_etf_symbols=broad_etf_symbols,
    )
    fills = []
    for ci in decision.cleared:
        fills.append(gate.execute(gate.preview(ci)))
        caps_state.daily_order_count += 1
    return CycleResult(
        cleared=len(decision.cleared),
        rejected=decision.rejected,
        fills=tuple(fills),
    )
