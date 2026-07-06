"""flows 조회 표면 — 공식 API에 없는 tossctl 고유 강점 (§S3, IMPL-06의 적재 원천)."""

from __future__ import annotations

from quantbot.adapter.tossctl.contracts import Flows, call
from quantbot.adapter.tossctl.proc import TossctlRunner


def flows(runner: TossctlRunner, symbol: str) -> Flows:
    return call(runner, ["quote", "flows", symbol], Flows)
