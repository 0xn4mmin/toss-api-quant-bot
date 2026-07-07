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


def test_regime_warmup_holds_cash_instead_of_crashing():
    """워크포워드 워밍업: 지수 이력 < ma_len 이면 예외가 아니라 현금 (2026-07-07 실측)."""
    import numpy as np
    from quantbot.strategy.slots.pipeline import build_us_core_signal

    closes = {s: 100.0 * np.cumprod(1 + np.full(71, 0.001))
              for s in ("AAA", "BBB", "SPX", "VIX")}

    class View:
        symbols = tuple(sorted(closes))
        def close(self, s):
            return closes[s]

    fn = build_us_core_signal(
        {"lookback": 65, "skip": 5, "abs_filter": True, "n": 2, "exit_buffer": 1.5,
         "ma_len": 100, "vix_threshold": 25.0, "e_min": 0.2, "caution_exposure": 0.5},
        cap=0.99, index_symbol="SPX", vix_symbol="VIX",
    )
    assert fn(View(), None) == {}                  # 판정 불가 = 노출 안 함

    long_closes = {s: np.concatenate([c, c[-1] * np.cumprod(1 + np.full(60, 0.001))])
                   for s, c in closes.items()}

    class LongView(View):
        def close(self, s):
            return long_closes[s]

    assert fn(LongView(), None) != {}              # 이력이 차면 정상 판정


def test_crashed_first_run_does_not_become_reproduction(registry, uptrend_store):
    """oos_opened 기록 후 죽은 실행 — 재실행이 '재현'으로 오인돼 생명주기 전이가
    생략되면 안 된다 (2026-07-07 실측에서 발견)."""
    from quantbot.backtest import judge, prereg, walkforward
    from conftest import LOOSE_GATES, LOW_COSTS, SMALL_METH, momentum_top1

    grid = {"lookback": [3, 5]}
    rng = (uptrend_store.date(0), uptrend_store.date(len(uptrend_store) - 1))
    prereg.seal(registry, "crashy", grid, rng, walkforward.folds_spec(SMALL_METH))

    def exploding(view, params):
        raise RuntimeError("simulated crash mid-run")

    with pytest.raises(RuntimeError):
        judge.evaluate_oos(registry, uptrend_store, "crashy", grid, rng,
                           exploding, LOW_COSTS, SMALL_METH, LOOSE_GATES)
    assert len(registry.events(judge.EVENT_OOS_OPENED)) == 1  # 개봉은 기록됨

    res = judge.evaluate_oos(registry, uptrend_store, "crashy", grid, rng,
                             momentum_top1, LOW_COSTS, SMALL_METH, LOOSE_GATES)
    assert res.reproduction is False               # 최초 판정으로 취급
    assert res.transition in ("paper", "rejected")  # 전이가 생략되지 않는다
    assert len(registry.transitions("crashy")) == 1


# ── INV-01a (2026-07-07 승인) — 분산형 ETF 캡 예외의 이중 검증 ──────────


def test_broad_etf_cap_eligible_requires_both_gates():
    from quantbot.engine.invariants import broad_etf_cap_eligible

    broad = frozenset({"SPY", "TLT"})
    assert broad_etf_cap_eligible("SPY", broad, "FOREIGN_ETF", "1.0")
    assert not broad_etf_cap_eligible("QQQ", broad, "FOREIGN_ETF", "1.0")   # 목록 밖
    assert not broad_etf_cap_eligible("SPY", broad, "FOREIGN_ETF", "3.0")   # 레버리지
    assert not broad_etf_cap_eligible("SPY", broad, "FOREIGN_ETF", None)    # 판정 불가
    assert not broad_etf_cap_eligible("SPY", broad, "FOREIGN_STOCK", "1.0") # 비ETF


def test_caps_applies_etf_cap_only_to_verified_set():
    """검증된 ETF는 50%까지, 목록 밖 개별 주식은 여전히 12%."""
    state = caps.CapsState()
    state.start_day(5_000_000.0)
    intents = [
        caps.OrderIntent("SPY", "BUY", amount_krw=1_000_000),   # 20% — ETF 캡 안
        caps.OrderIntent("AAPL", "BUY", amount_krw=1_000_000),  # 20% — INV-01 위반
    ]
    d = caps.check(intents, INV, state, equity_krw=5_000_000.0,
                   cash_krw=5_000_000.0, position_value_krw={},
                   broad_etf_symbols=frozenset({"SPY"}))
    assert {c.symbol for c in d.cleared} == {"SPY"}
    assert "INV-01" in d.rejected[0][1] and d.rejected[0][0].symbol == "AAPL"


def test_static_check_etf_sleeve_dual_verification():
    from quantbot.engine.approval import static_invariant_check
    from quantbot.strategy.schema import parse_strategy
    from test_strategy import _valid_dict

    d = _valid_dict()
    d["entry_exit"]["entry"]["params"]["n"] = 2   # alloc 1.0 / 2 = 50%
    strategy = parse_strategy(d)
    broad = frozenset({"SPY", "TLT"})
    master_ok = {"SPY": ("FOREIGN_ETF", "1.0"), "TLT": ("FOREIGN_ETF", "1.0")}
    ok = static_invariant_check(
        strategy, INV, {"us_core": ["SPY", "TLT"]},
        whitelist={"us_core": {"SPY", "TLT"}},
        stock_master=master_ok, broad_etf_symbols=broad,
    )
    assert ok == []                                # 50% ≤ ETF 캡 50%
    # 기계 검증 실패(레버리지 값) → 캡 강등이 아니라 위반
    master_bad = {"SPY": ("FOREIGN_ETF", "1.0"), "TLT": ("FOREIGN_ETF", "3.0")}
    bad = static_invariant_check(
        strategy, INV, {"us_core": ["SPY", "TLT"]},
        whitelist={"us_core": {"SPY", "TLT"}},
        stock_master=master_bad, broad_etf_symbols=broad,
    )
    assert any("INV-01a: TLT 기계 검증 실패" in v for v in bad)
    assert any("최악 비중 0.5000 > 캡 0.1200" in v for v in bad)  # 12%로 재판정
    # 혼합 sleeve(주식 포함)는 예외 없음 — 12% 적용
    mixed = static_invariant_check(
        strategy, INV, {"us_core": ["SPY", "AAPL"]},
        whitelist={"us_core": {"SPY", "AAPL"}},
        stock_master={**master_ok, "AAPL": ("FOREIGN_STOCK", None)},
        broad_etf_symbols=broad,
    )
    assert any("INV-01:" in v and "0.5000" in v for v in mixed)


def test_effective_cap_and_dual_momentum_excludes_vix():
    import numpy as np
    from quantbot.cli import effective_cap
    from quantbot.engine.invariants import load_invariants
    from quantbot.strategy.slots.pipeline import build_dual_momentum_signal

    inv = load_invariants()
    broad = {"SPY", "EFA", "TLT", "GLD", "IEF"}
    assert effective_cap(inv, {"SPY", "TLT"}, broad) == 0.50
    assert effective_cap(inv, {"SPY", "AAPL"}, broad) == 0.12   # 혼합 — 예외 없음
    assert effective_cap(inv, set(), broad) == 0.12

    closes = {s: 100.0 * np.cumprod(1 + g * np.ones(60))
              for s, g in {"SPY": 0.002, "TLT": 0.001, "VIX": 0.05}.items()}

    class View:
        symbols = tuple(sorted(closes))
        def close(self, s):
            return closes[s]

    fn = build_dual_momentum_signal({"lookback": 30, "top_n": 1}, cap=0.50,
                                    excluded=frozenset({"VIX"}))
    w = fn(View(), None)
    assert "VIX" not in w and w == {"SPY": 0.50}  # VIX(최고 상승)가 자산으로 안 뽑힘
