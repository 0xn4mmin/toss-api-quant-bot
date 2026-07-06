"""push listen 스트림 표면 (§I3, Phase 5) — JSONL을 typed 이벤트로 변환한다.

주 실시간 소스는 공식 REST 폴링이고 이 스트림은 보조다 (ARCH v1.1: 공식 API는
REST만 제공). 라인이 계약을 벗어나면 예외로 죽는 게 아니라 SchemaDrift 신호를
이벤트로 흘린다 — 감시자(watcher)가 이를 fail-safe hold 트리거로 소비한다.

주의(§I8): 이벤트 필드는 실측 전 초안이다.
"""

from __future__ import annotations

from typing import Iterator, Literal

from quantbot.adapter.contracts import Contract, SchemaDrift, SchemaDriftError, validate
from quantbot.adapter.tossctl.proc import TossctlBadJson, TossctlRunner

SOURCE = "tossctl"
LISTEN_ARGS = ["push", "listen"]


class PushEvent(Contract):
    """스트림 이벤트 — heartbeat·체결·시세 통지 (실측 전 초안)."""

    type: Literal["heartbeat", "fill", "quote", "error"]
    ts: str
    symbol: str | None = None
    price: float | None = None
    qty: float | None = None
    side: str | None = None
    detail: str | None = None


def events(runner: TossctlRunner) -> Iterator[PushEvent | SchemaDrift]:
    """typed 이벤트 스트림 — 계약 이탈·비JSON 라인은 SchemaDrift 신호로 흘린다."""
    try:
        for raw in runner.stream_json_lines(LISTEN_ARGS):
            try:
                yield validate(SOURCE, tuple(LISTEN_ARGS), raw, PushEvent)
            except SchemaDriftError as e:
                yield e.drift
    except TossctlBadJson as e:
        yield SchemaDrift(
            source=SOURCE, command=tuple(LISTEN_ARGS),
            model=PushEvent.__name__, detail=str(e),
        )
