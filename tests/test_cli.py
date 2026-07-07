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
    assert out.count("OK    ") == 17 and "DRIFT GET" not in out


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


def test_backtest_command_end_to_end(tmp_path, capsys, monkeypatch):
    """cli backtest — 전략 파일·그리드·CSV로 봉인→판정까지 한 번에 (사용자 절차 그대로)."""
    import numpy as np
    from conftest import gentle_uptrend, trading_dates, write_csv

    n = 480  # train 60 + test 30 폴드가 나오도록 소형 방법론을 씀
    dates = trading_dates("2018-01-02", n)
    closes = gentle_uptrend(n, seed=5, symbols=("AAA", "BBB", "CCC"))
    csv_path = write_csv(tmp_path / "d.csv", dates, closes)

    small_cfg = tmp_path / "backtest.yaml"
    small_cfg.write_text("""\
version: 1
methodology:
  train_days: 120
  test_days: 60
  step_days: 60
  rebalance_every_n_days: 5
  trading_days_per_week: 5
  trading_days_per_year: 252
  initial_capital_krw: 5000000
  bootstrap:
    n_samples: 200
    block_len: 5
    seed: 20260706
costs:
  commission_rate: 0.0001
  min_commission_krw: 1
  slippage_rate: 0.0005
  sell_tax_rate: 0
  annual_gain_tax_rate: 0
  annual_deduction_krw: 0
""", encoding="utf-8")
    small_gates = tmp_path / "gates.yaml"
    small_gates.write_text("""\
version: 1
g1_max_oos_mdd: 0.15
g2_mdd_percentile: 95
g2_p95_mdd_max: 0.20
g2_stress_mdd_max: 0.20
g2_stress_windows:
  - "2018-03-01/2018-03-31"
g3_min_cagr: 0.0
g3_min_sharpe: 0.5
g4_plateau_min_ratio: 0.7
g5_oos_is_min_ratio: 0.5
""", encoding="utf-8")
    small_grid = tmp_path / "grid.yaml"
    small_grid.write_text("""\
lookback_wk:
  - 2
  - 3
skip_wk:
  - 1
abs_filter:
  - true
n:
  - 2
exit_buffer:
  - 1.5
""", encoding="utf-8")

    # 스트레스 창(2018-03)이 OOS 시작(train 120일 뒤 ≈ 2018-06) 앞 —
    # 봉인 전 거부가 먼저 발화해야 한다 (v2 id 소모 사건 재발 방지)
    with pytest.raises(SystemExit, match="스트레스 창"):
        cli.main([
            "backtest", "--strategy", "strategies/momentum-core.v1.yaml",
            "--grid", str(small_grid), "--config", str(small_cfg),
            "--gates", str(small_gates), "--data", str(csv_path),
            "--allow-no-regime", "--var-dir", str(tmp_path / "var"),
        ])
    rc = cli.main([
        "backtest", "--strategy", "strategies/momentum-core.v1.yaml",
        "--grid", str(small_grid), "--config", str(small_cfg),
        "--gates", str(small_gates), "--data", str(csv_path), "--allow-no-regime",
        "--allow-uncovered-stress", "--var-dir", str(tmp_path / "var"),
    ])
    out = capsys.readouterr().out
    assert "사전등록 봉인" in out and "order_unit=fractional" in out
    assert "BT-G1:" in out and "selected =" in out
    assert rc in (0, 1)  # 판정 완주 (paper/rejected 둘 다 정상 종료)

    # 그리드 1칸 수정 후 재실행 → 재봉인 거부 (BT-02가 CLI 경로에서도 강제)
    small_grid.write_text(small_grid.read_text().replace("- 3", "- 4"), encoding="utf-8")
    with pytest.raises(Exception, match="BT-02"):
        cli.main([
            "backtest", "--strategy", "strategies/momentum-core.v1.yaml",
            "--grid", str(small_grid), "--config", str(small_cfg),
            "--gates", str(small_gates), "--data", str(csv_path), "--allow-no-regime",
            "--allow-uncovered-stress", "--var-dir", str(tmp_path / "var"),
        ])


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


def test_fetch_candles_survives_per_symbol_failure(official_server, tmp_path, capsys):
    """한 종목 404가 전체 적재를 죽이지 않는다 — 실패 보고 + exit 1."""
    import json as _j

    runtime = write_runtime(tmp_path, official_server.base_url)
    body = _j.dumps({"error": {"code": "stock-not-found", "message": "없음"}}).encode()
    official_server.fail_queue.append((404, {}, body))   # 첫 심볼의 첫 GET에서 404
    out = tmp_path / "c.csv"
    rc = cli.main(["--runtime", runtime, "fetch-candles",
                   "--symbols", "BAD,AAPL", "--days", "3", "--out", str(out)])
    printed = capsys.readouterr().out
    assert rc == 1
    assert "BAD: 실패" in printed and "AAPL: 3봉" in printed
    assert "date,symbol,close" in out.read_text()        # 성공분은 기록됨


def test_import_vix_require_start_drops_short_history(tmp_path, capsys):
    """짧은 이력 종목이 전체 창을 자르지 못한다 (2026-07-07 HON 사건 재발 방지)."""
    import csv

    candles = tmp_path / "candles.csv"
    with open(candles, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "symbol", "close", "traded_value"])
        for d in ("2016-12-15", "2021-05-11", "2021-05-12"):
            w.writerow([d, "SPY", 100, 0])
        for d in ("2021-05-11", "2021-05-12"):        # 짧은 이력 — 제외 대상
            w.writerow([d, "HON", 200, 0])
    cboe = tmp_path / "vix.csv"
    cboe.write_text("DATE,OPEN,HIGH,LOW,CLOSE\n"
                    "12/15/2016,10,10,10,11.0\n"
                    "05/11/2021,20,20,20,20.0\n"
                    "05/12/2021,21,21,21,21.0\n", encoding="utf-8")
    out = tmp_path / "m.csv"
    rc = cli.main(["import-vix", "--data", str(candles), "--cboe-csv", str(cboe),
                   "--out", str(out), "--require-start", "2017-02-01"])
    printed = capsys.readouterr().out
    assert rc == 0
    assert "HON: 이력 시작 2021-05-11" in printed     # 제외 보고
    assert "2016-12-15~" in printed                   # 창이 보존된다
    # 전 종목이 짧으면 실패 (창 확보 불가)
    with open(candles, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "symbol", "close", "traded_value"])
        for d in ("2021-05-11", "2021-05-12"):
            w.writerow([d, "SPY", 100, 0])
    with pytest.raises(SystemExit, match="require-start"):
        cli.main(["import-vix", "--data", str(candles), "--cboe-csv", str(cboe),
                  "--out", str(out), "--require-start", "2017-02-01"])
