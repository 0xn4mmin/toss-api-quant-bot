"""CLI 조립 루트 검사 — api-verify(계약 실측 도구)와 collect-flows의 end-to-end."""

from __future__ import annotations

import pytest

from quantbot import cli
from conftest import FAKE_TOSSCTL


def write_runtime(tmp_path, official_base_url: str | None = None) -> str:
    """테스트용 runtime.yaml — 로컬 서버·가짜 tossctl을 가리킨다."""
    (tmp_path / "id").write_text("cid-test", encoding="utf-8")
    (tmp_path / "sec").write_text("sec-test", encoding="utf-8")
    groups = "\n".join(
        f"      {g}: 10000"
        for g in ("AUTH", "ACCOUNT", "ASSET", "STOCK", "MARKET_INFO", "MARKET_DATA",
                  "MARKET_DATA_CHART", "ORDER_HISTORY", "ORDER_INFO")
    )
    text = f"""\
live_trading: false
adapter:
  official:
    base_url: "{official_base_url or 'https://unused.invalid'}"
    client_id_path: "{tmp_path / 'id'}"
    client_secret_path: "{tmp_path / 'sec'}"
    account_seq: "1"
    timeout_s: 5
    max_retries: 1
    backoff_base_s: 0
    rate_limits_tps:
{groups}
  tossctl:
    binary: "{FAKE_TOSSCTL}"
    timeout_s: 5
    max_retries: 1
    backoff_base_s: 0
    rate_min_interval_s: 0
"""
    p = tmp_path / "runtime.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_api_verify_all_ok(official_server, tmp_path, capsys):
    """전 조회 표면 실호출 → 계약 일치 → exit 0. 키를 놓는 즉시 실 API에 쓸 도구."""
    runtime = write_runtime(tmp_path, official_server.base_url)
    rc = cli.main(["--runtime", runtime, "api-verify",
                   "--symbols", "005930,AAPL", "--account"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("OK    ") == 16 and "DRIFT GET" not in out


def test_api_verify_reports_drift_and_fails(official_server, tmp_path, capsys):
    """계약 이탈 응답 → DRIFT 보고 + exit 1 — 계약 확정(§I8)의 신호."""
    official_server.fixtures["/api/v1/exchange-rate"]["rate"] = 1352.30  # str → number
    runtime = write_runtime(tmp_path, official_server.base_url)
    rc = cli.main(["--runtime", runtime, "api-verify", "--symbols", "005930"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "DRIFT GET /exchange-rate" in out
    assert "계약(contracts.py)을 실측에 맞춰 확정" in out


def test_collect_flows_end_to_end(tmp_path, monkeypatch, capsys):
    """cron이 부를 명령 그대로: 스크리너 유니버스 + 추가 종목 → flows.db 적재."""
    from quantbot.collect.flows_snapshot import FlowsStore
    from quantbot.engine.registry import Registry
    from test_flows_snapshot import DAYS5, write_flows_fixture
    from conftest import TOSSCTL_FIXTURES
    import shutil

    fx = tmp_path / "fx"
    shutil.copytree(TOSSCTL_FIXTURES, fx)          # market_screener → 005930, 000660
    write_flows_fixture(fx, "005930", DAYS5)
    write_flows_fixture(fx, "000660", DAYS5)
    write_flows_fixture(fx, "035420", DAYS5)       # --symbols 추가 종목
    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(fx))
    runtime = write_runtime(tmp_path)
    var_dir = tmp_path / "var"

    rc = cli.main(["--runtime", runtime, "collect-flows",
                   "--screener", "kr_flows", "--symbols", "035420",
                   "--var-dir", str(var_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "신규 15행 / 3종목" in out and "백필 기록됨" in out
    with FlowsStore(var_dir / "flows.db") as store:
        assert len(store.rows("000660")) == 5
    with Registry(var_dir / "registry.db") as reg:
        assert len(reg.events("flows_backfill")) == 1

    # 이틀째(같은 데이터) — 중복 없음, exit 0
    rc2 = cli.main(["--runtime", runtime, "collect-flows",
                    "--screener", "kr_flows", "--symbols", "035420",
                    "--var-dir", str(var_dir)])
    assert rc2 == 0
    assert "신규 0행" in capsys.readouterr().out


def test_collect_flows_failure_exits_nonzero(tmp_path, monkeypatch, capsys):
    """적재 실패 → exit 1 — cron 메일이 임시 경고 채널이 되는 지점."""
    from test_flows_snapshot import DAYS5, write_flows_fixture

    fx = tmp_path / "fx"
    write_flows_fixture(fx, "005930", DAYS5)
    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(fx))
    runtime = write_runtime(tmp_path)
    rc = cli.main(["--runtime", runtime, "collect-flows",
                   "--symbols", "005930,NOFIX", "--var-dir", str(tmp_path / "var")])
    assert rc == 1
    assert "실패 1건" in capsys.readouterr().out
