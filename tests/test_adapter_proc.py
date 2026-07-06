"""proc.py 검사 — 인자 배열(인젝션 불가)·rate limit·재시도 정책·주문 네임스페이스 차단.

가짜 tossctl을 진짜 subprocess로 실행하므로 호출의 물리학 전체가 실코드 경로다.
"""

from __future__ import annotations

import json

import pytest

from quantbot.adapter.proc import (
    OrderNamespaceBlocked,
    RunPolicy,
    TossctlBadJson,
    TossctlError,
    TossctlFailed,
    TossctlRunner,
    TossctlTimeout,
    attempts_for,
    is_order_family,
)
from conftest import TOSSCTL_FIXTURES, make_run_policy


def test_args_must_be_string_array(tossctl_runner):
    for bad in ("doctor", [], ["quote", 5], None):
        with pytest.raises(TossctlError):
            tossctl_runner.run_json(bad)  # type: ignore[arg-type]


def test_injection_is_impossible_args_passed_literally(tossctl_runner, monkeypatch):
    """셸 문자열 조립 경로가 없다 — 메타문자가 단일 argv 원소로 그대로 전달된다."""
    monkeypatch.setenv("FAKE_TOSSCTL_DUMP_ARGS", "1")
    evil = "$(rm -rf /tmp/pwned); `id` && echo hacked | tee x"
    argv = tossctl_runner.run_json(["quote", "get", evil])
    assert argv == ["quote", "get", evil, "--output", "json"]


def test_order_family_attempts_is_structurally_one():
    """DoD: 주문 계열 재시도 없음 — 정책이 아니라 attempts_for의 반환값이다."""
    policy = make_run_policy(max_retries=5)
    assert is_order_family(["order", "place"])
    assert attempts_for(["order", "place"], policy) == 1
    assert attempts_for(["order", "preview"], policy) == 1
    assert attempts_for(["quote", "get", "AAPL"], policy) == 6
    assert attempts_for(["orders", "list"], policy) == 6  # ledger 조회는 주문 계열이 아님


def test_order_namespace_blocked_in_query_surface(tossctl_runner):
    """Phase 2 조회 표면에서 주문 네임스페이스는 실행 자체가 안 된다."""
    with pytest.raises(OrderNamespaceBlocked):
        tossctl_runner.run_json(["order", "preview", "AAPL", "1"])


def test_order_family_never_retries_even_when_enabled(tmp_path, monkeypatch):
    """주문 네임스페이스를 켠 러너(Phase 4 가정)도 실패 시 딱 1회만 시도한다."""
    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(TOSSCTL_FIXTURES))
    fail_file = tmp_path / "fails"
    fail_file.write_text("3")
    monkeypatch.setenv("FAKE_TOSSCTL_FAIL_FILE", str(fail_file))
    runner = TossctlRunner(make_run_policy(max_retries=5), enable_order_namespace=True)
    with pytest.raises(TossctlFailed):
        runner.run_json(["order", "cancel", "ord-1"])
    assert fail_file.read_text() == "2", "재시도가 일어났다 — 주문 계열 금지 위반"


def test_query_family_retries_then_succeeds(tossctl_runner, tmp_path, monkeypatch):
    """조회 계열은 지수 백오프 재시도 — 2회 실패 후 성공."""
    fail_file = tmp_path / "fails"
    fail_file.write_text("2")
    monkeypatch.setenv("FAKE_TOSSCTL_FAIL_FILE", str(fail_file))
    data = tossctl_runner.run_json(["doctor"])
    assert data["ok"] is True
    assert fail_file.read_text() == "0"


def test_query_family_exhausts_retries(tossctl_runner, tmp_path, monkeypatch):
    fail_file = tmp_path / "fails"
    fail_file.write_text("9")
    monkeypatch.setenv("FAKE_TOSSCTL_FAIL_FILE", str(fail_file))
    with pytest.raises(TossctlFailed):
        tossctl_runner.run_json(["doctor"])
    assert fail_file.read_text() == "6"  # 1 + max_retries(2) = 3회 시도


def test_timeout_raises(monkeypatch):
    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(TOSSCTL_FIXTURES))
    monkeypatch.setenv("FAKE_TOSSCTL_SLEEP", "2.0")
    runner = TossctlRunner(make_run_policy(timeout_s=0.3, max_retries=0))
    with pytest.raises(TossctlTimeout):
        runner.run_json(["doctor"])


def test_broken_json_is_immediate_signal_not_retry(tossctl_runner, tmp_path, monkeypatch):
    """JSON 자체가 깨진 응답은 재시도가 아니라 즉시 상향 신호다."""
    bad_dir = tmp_path / "fx"
    bad_dir.mkdir()
    (bad_dir / "doctor.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(bad_dir))
    with pytest.raises(TossctlBadJson):
        tossctl_runner.run_json(["doctor"])


def test_rate_limiter_enforces_min_interval():
    """호출 간 최소 간격 — 주입 시계로 결정적으로 검증 (RISK-01)."""
    sleeps: list[float] = []
    # _rate_limit는 호출당 clock을 1~2회 읽는다: (elapsed 계산) + (last 갱신)
    clock_values = iter([0.0, 0.10, 0.10, 1.0, 1.0])
    runner = TossctlRunner(
        make_run_policy(rate_min_interval_s=0.35),
        sleep=sleeps.append,
        clock=lambda: next(clock_values),
    )
    limiter = runner._rate_limit
    limiter()                 # 첫 호출 — 대기 없음
    limiter()                 # 0.10초 경과 — 0.25초 대기해야 함
    limiter()                 # 0.90초 경과 — 대기 없음
    assert sleeps == [pytest.approx(0.25)]


def test_policy_loads_from_runtime_yaml():
    """수치는 코드가 아니라 config/runtime.yaml adapter 섹션에서 온다."""
    policy = RunPolicy.from_runtime_yaml("config/runtime.yaml")
    assert policy.binary == "tossctl"
    assert policy.max_retries >= 0 and policy.rate_min_interval_s > 0
