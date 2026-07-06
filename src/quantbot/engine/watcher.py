"""이벤트 감시자 (IMPL-04, GATE-05) — 손절 평가 + fail-safe hold의 발동 지점.

기본 정책은 전면 동결이다: 이상(SchemaDrift·하트비트 부재·연속 오류) 감지 시
신규 주문뿐 아니라 자동 손절도 중단한다 — 오염된 데이터 위의 손절 오발동이
동결보다 기대 손실이 크기 때문 (§9). 봇이 스스로 hold를 해제하는 경로는 없다:
해제는 Tier-2(사람의 confirm token, Phase 6 라우터)가 release_hold를 부를 때뿐.

발동 = registry 이벤트 + caps 전면 거부 + escalation 콜백 (텔레그램은 Phase 6,
그 전까지 콜백은 로그).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping

from quantbot import _yaml
from quantbot.adapter.contracts import SchemaDrift
from quantbot.adapter.tossctl.stream import PushEvent
from quantbot.engine.caps import CapsState
from quantbot.engine.registry import Registry

log = logging.getLogger("quantbot.engine.watcher")

EVENT_HOLD = "fail_safe_hold"
EVENT_HOLD_RELEASED = "fail_safe_hold_released"
EVENT_STOP_LOSS = "stop_loss_breach"


class WatcherConfigError(ValueError):
    pass


@dataclass(frozen=True)
class WatcherConfig:
    heartbeat_timeout_s: float
    max_consecutive_errors: int

    @classmethod
    def from_config(cls, cfg: dict) -> "WatcherConfig":
        for key in ("heartbeat_timeout_s", "max_consecutive_errors"):
            v = cfg.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise WatcherConfigError(f"watcher.{key}: 양수 필요: {v!r}")
        return cls(
            heartbeat_timeout_s=float(cfg["heartbeat_timeout_s"]),
            max_consecutive_errors=int(cfg["max_consecutive_errors"]),
        )

    @classmethod
    def from_runtime_yaml(cls, path: str | Path) -> "WatcherConfig":
        data = _yaml.load_file(str(path))
        w = data.get("watcher")
        if not isinstance(w, dict):
            raise WatcherConfigError(f"{path}: watcher 섹션이 없다")
        return cls.from_config(w)


@dataclass(frozen=True)
class StopLossBreach:
    """손절 조건 충족 통지 — 주문이 아니다. 주문 의도는 엔진이 caps/gate로 만든다."""

    symbol: str
    price: float
    entry_price: float
    stop_loss_pct: float


@dataclass
class Watcher:
    registry: Registry
    caps_state: CapsState
    config: WatcherConfig
    # symbol → (entry_price, stop_loss_pct) — 보유·전략 선언에서 엔진이 주입
    positions: Callable[[], Mapping[str, tuple[float, float]]]
    escalate: Callable[[str, dict], None] = lambda reason, ctx: log.critical(
        "ESCALATION %s %s", reason, ctx
    )
    clock: Callable[[], float] = time.monotonic
    _last_event_at: float | None = field(default=None, init=False)
    _consecutive_errors: int = field(default=0, init=False)

    # ── fail-safe hold ──────────────────────────────────────────────────

    def hold(self, reason: str, context: dict | None = None) -> None:
        if self.caps_state.hold:
            return  # 이미 동결 — 중복 발동은 기록만 남기지 않는다
        self.caps_state.hold = True
        payload = {"reason": reason, **(context or {})}
        self.registry.append_event(EVENT_HOLD, "critical", payload)
        self.escalate(reason, payload)
        log.critical("fail-safe hold 발동: %s %s", reason, payload)

    def release_hold(self, *, confirmed_by_tier2: bool, detail: str) -> None:
        """해제는 사람의 2단계 승인 뒤에만 — 봇 스스로 부르는 경로가 없다 (§9)."""
        if not confirmed_by_tier2:
            raise PermissionError("hold 해제는 Tier-2 confirm 없이는 불가능하다 (GATE-05)")
        self.caps_state.hold = False
        self._consecutive_errors = 0
        self.registry.append_event(EVENT_HOLD_RELEASED, "audit", {"detail": detail})

    # ── 이벤트 소비 ─────────────────────────────────────────────────────

    def check_heartbeat(self) -> None:
        """폴링 루프가 주기적으로 부른다 — 마지막 이벤트 이후 침묵이 길면 hold."""
        if self._last_event_at is None:
            self._last_event_at = self.clock()
            return
        silence = self.clock() - self._last_event_at
        if silence > self.config.heartbeat_timeout_s and not self.caps_state.hold:
            self.hold("heartbeat_lost", {"silence_s": round(silence, 1)})

    def process(self, events: Iterable[PushEvent | SchemaDrift]) -> list[StopLossBreach]:
        """재생 가능한 이벤트 스트림 소비 — 손절 통지 목록을 반환한다."""
        breaches: list[StopLossBreach] = []
        for ev in events:
            self._last_event_at = self.clock()
            if isinstance(ev, SchemaDrift):
                # 비공식 API 스키마 변경 흡수 지점 → 전면 동결 (§I3)
                self.hold("schema_drift", {
                    "command": list(ev.command), "model": ev.model,
                    "detail": ev.detail[:300],
                })
                continue
            if ev.type == "error":
                self._consecutive_errors += 1
                if self._consecutive_errors >= self.config.max_consecutive_errors:
                    self.hold("consecutive_errors", {
                        "count": self._consecutive_errors, "detail": ev.detail,
                    })
                continue
            self._consecutive_errors = 0
            if ev.type == "quote" and ev.symbol and ev.price is not None:
                if self.caps_state.hold:
                    continue  # 동결 중엔 자동 손절도 중단 (§9)
                pos = self.positions().get(ev.symbol)
                if pos is None:
                    continue
                entry, stop_pct = pos
                if ev.price <= entry * (1.0 - stop_pct):
                    breach = StopLossBreach(ev.symbol, ev.price, entry, stop_pct)
                    breaches.append(breach)
                    self.registry.append_event(EVENT_STOP_LOSS, "warning", {
                        "symbol": ev.symbol, "price": ev.price,
                        "entry_price": entry, "stop_loss_pct": stop_pct,
                    })
        return breaches
