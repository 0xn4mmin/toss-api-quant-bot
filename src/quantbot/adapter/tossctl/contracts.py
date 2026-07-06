"""tossctl 명령별 계약 (IMPL-03 v1.1) — 읽기 전용 3표면 + 헬스.

공식 Open API가 제공하지 않는 조회만 남는다: flows(수급)·지수·스크리너.
주의(§I8): 필드 구성은 실측 전 초안 — 실제 tossctl 응답과 다르면 실측으로 확정한다.
"""

from __future__ import annotations

from quantbot.adapter.contracts import Contract, validate
from quantbot.adapter.tossctl.proc import TossctlRunner

SOURCE = "tossctl"


def call(runner: TossctlRunner, args: list[str], model: type[Contract]):
    """proc 실행 → JSON 파싱 → 계약 검증의 3단 — tossctl 표면의 유일한 통과 경로."""
    data = runner.run_json(args)
    return validate(SOURCE, tuple(args), data, model)


# ═══ health — doctor · auth status ══════════════════════════════════════


class DoctorCheck(Contract):
    name: str
    status: str
    detail: str | None = None


class DoctorReport(Contract):
    ok: bool
    checks: list[DoctorCheck]


class AuthStatus(Contract):
    authenticated: bool
    expires_at: str | None = None


# ═══ flows — 투자자별 수급 (KR 새틀라이트의 시그널 원천, §S3) ═══════════


class FlowRow(Contract):
    date: str
    foreign_net: float
    inst_net: float
    traded_value: float


class Flows(Contract):
    symbol: str
    rows: list[FlowRow]


# ═══ market — 지수(레짐 필터 입력, §S4) · 스크리너 ═════════════════════


class IndexQuote(Contract):
    name: str
    value: float
    as_of: str


class ScreenerRow(Contract):
    symbol: str
    name: str


class ScreenerResult(Contract):
    preset: str
    rows: list[ScreenerRow]
