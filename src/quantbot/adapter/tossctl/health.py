"""tossctl 헬스 표면 — flows 경로의 생존 감시 (실패 누적 → fail-safe hold, §9)."""

from __future__ import annotations

from quantbot.adapter.tossctl.contracts import AuthStatus, DoctorReport, call
from quantbot.adapter.tossctl.proc import TossctlRunner


def doctor(runner: TossctlRunner) -> DoctorReport:
    return call(runner, ["doctor"], DoctorReport)


def auth_status(runner: TossctlRunner) -> AuthStatus:
    return call(runner, ["auth", "status"], AuthStatus)
