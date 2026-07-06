"""health.* — 어댑터 자체 헬스체크 표면 (ARCH-06: doctor · auth status).

실패 누적 시 fail-safe hold 트리거(§9)는 엔진(Phase 5 watcher)이 소비한다.
"""

from __future__ import annotations

from quantbot.adapter.contracts import AuthStatus, DoctorReport, call
from quantbot.adapter.proc import TossctlRunner


def doctor(runner: TossctlRunner) -> DoctorReport:
    return call(runner, ["doctor"], DoctorReport)


def auth_status(runner: TossctlRunner) -> AuthStatus:
    return call(runner, ["auth", "status"], AuthStatus)
