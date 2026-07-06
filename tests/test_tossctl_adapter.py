"""tossctl 어댑터(읽기 전용 보조) 검사 — allowlist·인젝션 불가·재시도·계약 드리프트.

가짜 tossctl을 진짜 subprocess로 실행하므로 호출의 물리학 전체가 실코드 경로다.
"""

from __future__ import annotations

import json
import shutil

import pytest

from quantbot.adapter.contracts import SchemaDriftError
from quantbot.adapter.tossctl import flows as flows_mod
from quantbot.adapter.tossctl import health, mkt
from quantbot.adapter.tossctl.proc import (
    ALLOWED_NAMESPACES,
    CommandNotAllowed,
    RunPolicy,
    TossctlBadJson,
    TossctlError,
    TossctlFailed,
    TossctlRunner,
    TossctlTimeout,
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
    argv = tossctl_runner.run_json(["quote", "flows", evil])
    assert argv == ["quote", "flows", evil, "--output", "json"]


def test_only_query_namespaces_are_expressible(tossctl_runner):
    """결정 1: 주문은 물론, allowlist 밖 어떤 네임스페이스도 실행 자체가 안 된다."""
    assert ALLOWED_NAMESPACES == ("quote", "market", "doctor", "auth")
    for forbidden in (["order", "place", "AAPL"], ["orders", "list"],
                      ["account", "summary"], ["watchlist", "add", "X"]):
        with pytest.raises(CommandNotAllowed):
            tossctl_runner.run_json(forbidden)


def test_query_retries_then_succeeds(tossctl_runner, tmp_path, monkeypatch):
    fail_file = tmp_path / "fails"
    fail_file.write_text("2")
    monkeypatch.setenv("FAKE_TOSSCTL_FAIL_FILE", str(fail_file))
    data = tossctl_runner.run_json(["doctor"])
    assert data["ok"] is True
    assert fail_file.read_text() == "0"


def test_query_exhausts_retries(tossctl_runner, tmp_path, monkeypatch):
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


def test_broken_json_is_immediate_signal_not_retry(tmp_path, monkeypatch):
    bad_dir = tmp_path / "fx"
    bad_dir.mkdir()
    (bad_dir / "doctor.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(bad_dir))
    runner = TossctlRunner(make_run_policy())
    with pytest.raises(TossctlBadJson):
        runner.run_json(["doctor"])


def test_rate_limiter_enforces_min_interval():
    sleeps: list[float] = []
    clock_values = iter([0.0, 0.10, 0.10, 1.0, 1.0])
    runner = TossctlRunner(
        make_run_policy(rate_min_interval_s=0.35),
        sleep=sleeps.append,
        clock=lambda: next(clock_values),
    )
    runner._rate_limit()
    runner._rate_limit()
    runner._rate_limit()
    assert sleeps == [pytest.approx(0.25)]


def test_policy_loads_from_runtime_yaml():
    policy = RunPolicy.from_runtime_yaml("config/runtime.yaml")
    assert policy.binary == "tossctl"
    assert policy.rate_min_interval_s > 0


# ── 표면 라운드트립 + 계약 드리프트 ──────────────────────────────────────


def test_query_surfaces_roundtrip(tossctl_runner):
    assert health.doctor(tossctl_runner).ok is True
    assert health.auth_status(tossctl_runner).authenticated is True
    f = flows_mod.flows(tossctl_runner, "005930")
    assert f.symbol == "005930" and f.rows[0].foreign_net > 0
    assert mkt.index(tossctl_runner, "SPX").value > 0
    assert len(mkt.screener(tossctl_runner, "kr_flows").rows) == 2


@pytest.mark.parametrize("mutation", ["extra", "missing", "wrong_type"])
def test_flows_contract_violation_rejected_whole(mutation, tmp_path, monkeypatch):
    """계약을 깨는 응답 픽스처 → SchemaDrift 전체 거부 (fail-closed)."""
    drift_dir = tmp_path / "fx"
    shutil.copytree(TOSSCTL_FIXTURES, drift_dir)
    obj = json.loads((drift_dir / "quote_flows.json").read_text(encoding="utf-8"))
    if mutation == "extra":
        obj["surprise"] = 1
    elif mutation == "missing":
        del obj["rows"][0]["foreign_net"]
    else:
        obj["rows"][0]["traded_value"] = "많음"
    (drift_dir / "quote_flows.json").write_text(json.dumps(obj, ensure_ascii=False))
    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(drift_dir))
    runner = TossctlRunner(make_run_policy())
    with pytest.raises(SchemaDriftError) as exc:
        flows_mod.flows(runner, "005930")
    assert exc.value.drift.source == "tossctl"
    assert exc.value.drift.command[:2] == ("quote", "flows")
