"""보안·정합 감사(2026-07-06)에서 나온 수정들의 회귀 고정.

발견 1: caps가 배치 내 같은 종목 매수 누적을 안 봐서 INV-01을 쪼개기로 우회 가능.
발견 2: 스트레스 창을 데이터가 커버 안 하면 G2가 조용히 공허 통과 (BT-G2 왜곡).
발견 3: 재시작이 fail-safe hold를 조용히 지움 (RISK-06 위반).
발견 4: 자격증명이 비-루프백 평문 HTTP로 나갈 수 있었음.
발견 5: CLI 백테스트가 전략이 선언한 레짐 필터를 무시함 (§S4 방어선 누락 판정).
"""

from __future__ import annotations

import dataclasses

import pytest

from quantbot.adapter.official.http import Credentials, OpenApiError, OpenApiPolicy
from quantbot.engine import caps
from quantbot.engine.invariants import load_invariants
from quantbot.engine.watcher import Watcher, WatcherConfig, restore_hold_state

INV = load_invariants()


def test_inv01_cannot_be_bypassed_by_order_splitting():
    """발견 1: 같은 종목 11%+11% 쪼개기 — 두 번째는 배치 누적으로 거부돼야 한다."""
    state = caps.CapsState()
    state.start_day(5_000_000.0)
    intents = [
        caps.OrderIntent("AAA", "BUY", amount_krw=550_000),  # 11%
        caps.OrderIntent("AAA", "BUY", amount_krw=550_000),  # 누적 22% > 12%
    ]
    d = caps.check(intents, INV, state, equity_krw=5_000_000.0,
                   cash_krw=5_000_000.0, position_value_krw={})
    assert len(d.cleared) == 1
    assert "INV-01" in d.rejected[0][1]


def test_uncovered_stress_window_fails_g2(registry, uptrend_store):
    """발견 2: 의무 스트레스 창을 OOS가 커버하지 못하면 G2 불합격 (공허 통과 금지)."""
    from quantbot.backtest import judge, prereg, walkforward
    from conftest import LOOSE_GATES, LOW_COSTS, SMALL_METH, momentum_top1

    gates = dataclasses.replace(
        LOOSE_GATES, g2_stress_windows=(("2020-02-01", "2020-04-30"),)  # 데이터 밖
    )
    rng = (uptrend_store.date(0), uptrend_store.date(len(uptrend_store) - 1))
    grid = {"lookback": [3, 5]}
    prereg.seal(registry, "uncovered", grid, rng, walkforward.folds_spec(SMALL_METH))
    res = judge.evaluate_oos(
        registry, uptrend_store, "uncovered", grid, rng,
        momentum_top1, LOW_COSTS, SMALL_METH, gates,
    )
    assert res.flags["BT-G2"] is False
    assert res.metrics["stress_windows_uncovered"] == ["2020-02-01/2020-04-30"]
    assert res.transition == "rejected"


def test_hold_survives_restart(registry):
    """발견 3: hold 발동 후 재시작 → registry에서 hold 복원."""
    state = caps.CapsState()
    w = Watcher(registry=registry, caps_state=state,
                config=WatcherConfig(90, 3), positions=dict,
                escalate=lambda r, c: None)
    w.hold("crash_before_release")
    fresh_state = caps.CapsState()             # 재시작 시뮬레이션
    assert restore_hold_state(registry, fresh_state) is True
    assert fresh_state.hold
    # 해제 기록이 있으면 복원 안 함
    w.release_hold(confirmed_by_tier2=True, detail="tier2")
    fresh2 = caps.CapsState()
    assert restore_hold_state(registry, fresh2) is False
    assert not fresh2.hold


def test_plaintext_http_refused_except_loopback():
    """발견 4: 자격증명 경로는 https 또는 루프백만."""
    base = dict(timeout_s=1, max_retries=0, backoff_base_s=0,
                rate_limits_tps={"AUTH": 5})
    OpenApiPolicy.from_config({"base_url": "http://127.0.0.1:9", **base})   # 허용
    OpenApiPolicy.from_config({"base_url": "https://openapi.tossinvest.com", **base})
    with pytest.raises(OpenApiError, match="평문 HTTP"):
        OpenApiPolicy.from_config({"base_url": "http://evil.example.com", **base})


def test_credentials_permission_warning(tmp_path, caplog):
    import logging

    (tmp_path / "id").write_text("cid", encoding="utf-8")
    (tmp_path / "sec").write_text("sec", encoding="utf-8")
    (tmp_path / "id").chmod(0o644)             # 그룹/전체 읽기 — 경고 대상
    (tmp_path / "sec").chmod(0o600)
    with caplog.at_level(logging.WARNING):
        Credentials.from_files(tmp_path / "id", tmp_path / "sec")
    assert any("chmod 600" in r.message for r in caplog.records)


def test_backtest_cli_refuses_silent_regime_drop(tmp_path):
    """발견 5: 전략이 레짐을 선언했는데 입력 없이 돌리면 명시 플래그 없이는 거부."""
    from quantbot import cli

    with pytest.raises(SystemExit, match="regime_filter"):
        cli.main([
            "backtest", "--strategy", "strategies/momentum-core.v1.yaml",
            "--grid", "config/grids/momentum-core.yaml",
            "--data", str(tmp_path / "none.csv"),  # 데이터 검사 전에 거부돼야 함
            "--var-dir", str(tmp_path / "var"),
        ])

def test_import_vix_merges_and_aligns_grid(tmp_path, capsys):
    """STRAT v1.3: CBOE VIX 병합 — 날짜 격자 교집합 정렬 후 스토어 로드 가능."""
    import csv

    from quantbot import cli
    from quantbot.backtest.data import MarketDataStore

    candles = tmp_path / "candles.csv"
    with open(candles, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "symbol", "close", "traded_value"])
        for d, c in [("2026-07-01", 100.0), ("2026-07-02", 101.0), ("2026-07-03", 102.0)]:
            w.writerow([d, "SPY", c, 1000])
            w.writerow([d, "AAPL", c * 2, 2000])
    cboe = tmp_path / "VIX_History.csv"
    cboe.write_text(
        "DATE,OPEN,HIGH,LOW,CLOSE\n"
        "06/30/2026,15,16,14,15.5\n"      # 교집합 밖 — 제외돼야 함
        "07/01/2026,15,16,14,15.0\n"
        "07/02/2026,16,17,15,16.2\n"
        "07/03/2026,17,18,16,17.1\n",
        encoding="utf-8",
    )
    out = tmp_path / "merged.csv"
    rc = cli.main(["import-vix", "--data", str(candles), "--cboe-csv", str(cboe),
                   "--out", str(out)])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "3종목 × 3일" in printed and "VIX: 교집합 밖 1일 제외" in printed
    store = MarketDataStore.from_csv(out)      # 완전 격자 정합 통과
    assert store.symbols == ("AAPL", "SPY", "VIX")
    assert store.close_at("VIX", 1) == pytest.approx(16.2)
