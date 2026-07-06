"""Phase 3 DoD — 전략 파일 파싱/정적 검사 통과·거부, 슬롯 의사코드 일치,
러너(Phase 1) end-to-end 완주."""

from __future__ import annotations

import numpy as np
import pytest

from quantbot.engine.approval import static_invariant_check
from quantbot.engine.invariants import load_invariants
from quantbot.strategy.loader import StrategyLoadError, load_strategy
from quantbot.strategy.schema import StrategySchemaError, parse_strategy
from quantbot.strategy.slots import kr_flows, regime, rules, trend_score
from quantbot.strategy.slots.pipeline import build_us_core_signal, cap_clip_redistribute


# ── 스키마·로더 ──────────────────────────────────────────────────────────


def test_example_strategy_file_parses():
    """저장소의 예시 전략 파일이 스키마를 통과한다."""
    s = load_strategy("strategies/momentum-core.v1.yaml")
    assert s.meta.id == "momentum-core" and s.meta.version == 1
    assert s.sizing.order_unit == "fractional"          # v1.1 결정 2
    assert s.entry_exit.exit.stop_loss_pct == 0.10
    assert [d.slot for d in s.signals] == ["trend_score", "regime_filter"]


def _valid_dict() -> dict:
    return {
        "meta": {"id": "t", "version": 1, "author": "human"},
        "universe": {"us_core": {"source": "whitelist", "max_symbols": 10}},
        "signals": [{"slot": "trend_score", "inputs": ["md.chart"],
                     "params": {"lookback": 26, "skip": 2}}],
        "entry_exit": {
            "entry": {"rule_slot": "rank_top_n", "params": {"n": 10}},
            "exit": {"rule_slot": "rank_drop", "params": {"exit_buffer": 1.5},
                     "stop_loss_pct": 0.10},
        },
        "sizing": {"scheme": "equal_weight_capped", "sleeves": {"us_core": 1.0},
                   "no_trade_band": 0.02, "order_unit": "whole"},
        "cadence": {"rebalance": "weekly", "execution_window": {"us": "23:00"}},
    }


@pytest.mark.parametrize("mutate, match", [
    (lambda d: d["sizing"].update(sleeves={"us_core": 0.9}), "합이 1"),
    (lambda d: d["meta"].update(author="llm"), "goal_prompt"),
    (lambda d: d.update(surprise=1), "extra"),
    (lambda d: d["entry_exit"]["exit"].update(stop_loss_pct=1.5), "stop_loss_pct"),
    (lambda d: d["universe"].update(kr={"source": "screener", "max_symbols": 4}),
     "preset"),
    (lambda d: d["cadence"].update(rebalance="daily"), "rebalance"),
    (lambda d: d["sizing"].update(order_unit="half"), "order_unit"),
])
def test_schema_rejects_violations(mutate, match):
    d = _valid_dict()
    mutate(d)
    with pytest.raises(StrategySchemaError, match=match):
        parse_strategy(d)


def test_loader_refuses_paths_outside_strategies_dir(tmp_path):
    p = tmp_path / "evil.yaml"
    p.write_text("meta:\n  id: x\n", encoding="utf-8")
    with pytest.raises(StrategyLoadError, match="아래에만"):
        load_strategy(p)


# ── 슬롯 ↔ 의사코드 (§S2/S3/S4) ─────────────────────────────────────────


def test_momentum_matches_pseudocode():
    """mom = close[-1-skip] / close[-1-skip-lookback] - 1."""
    c = np.array([100.0, 110.0, 121.0, 133.1, 146.41])  # 매일 +10%
    mom = trend_score.momentum({"A": c}, lookback=2, skip=1)
    assert mom["A"] == pytest.approx(c[-2] / c[-4] - 1.0)  # = 0.21
    assert trend_score.momentum({"A": c[:3]}, lookback=2, skip=1) == {}  # 이력 부족 = 부재


def test_rank_and_abs_filter():
    scores = {"A": 0.05, "B": 0.20, "C": -0.10, "D": 0.20}
    assert trend_score.rank_desc(scores) == ["B", "D", "A", "C"]  # 동점은 심볼순
    assert trend_score.apply_abs_filter(scores, True) == {"A": 0.05, "B": 0.20, "D": 0.20}


def test_select_holdings_hysteresis():
    """진입선(n)과 청산선(n×buffer) 분리 — 경계 왕복 매매 차단 (§S2)."""
    ranked = ["A", "B", "C", "D", "E", "F"]
    # 보유 D: rank 4 ≤ 2×1.5=3? 아니오 → 청산. 보유 C: rank 3 ≤ 3 → 유지.
    sel = rules.select_holdings(ranked, holdings={"C", "D"}, n=2, exit_buffer=1.5)
    assert sel == ["A", "C"]  # C 유지 + 빈 슬롯 1개를 최상위 A로
    # 신규 (보유 없음): 상위 n
    assert rules.select_holdings(ranked, holdings=set(), n=2, exit_buffer=1.5) == ["A", "B"]


def test_regime_exposure_three_tiers():
    up = np.linspace(90, 110, 200)     # 추세 위
    down = np.linspace(110, 90, 200)   # 추세 아래
    kw = dict(ma_len=100, vix_threshold=25.0, e_min=0.2, caution_exposure=0.5)
    assert regime.regime_exposure(up, 15.0, **kw) == 1.0     # risk-on
    assert regime.regime_exposure(up, 30.0, **kw) == 0.5     # caution (추세만)
    assert regime.regime_exposure(down, 15.0, **kw) == 0.5   # caution (VIX만)
    assert regime.regime_exposure(down, 30.0, **kw) == 0.2   # risk-off
    with pytest.raises(ValueError):
        regime.regime_exposure(up[:50], 15.0, **kw)          # 이력 부족 = 판정 불가


def test_kr_flows_score_matches_pseudocode():
    """score = Σ(외+기) ÷ Σ(거래대금), persistence = 순매수>0 일수 비율 (§S3)."""
    f = [100.0, -50.0, 200.0, 30.0, -10.0]
    i = [50.0, -30.0, 100.0, -40.0, 20.0]
    tv = [1000.0] * 5
    score, persist = kr_flows.flows_score(f, i, tv, window=5)
    assert score == pytest.approx((150 - 80 + 300 - 10 + 10) / 5000)
    assert persist == pytest.approx(3 / 5)                    # 순매수 양수 3일
    assert kr_flows.flows_score(f, i, tv, window=6) is None   # 표본 부족 = 부재
    cands = kr_flows.select_candidates(
        {"A": (0.1, 0.8), "B": (0.2, 0.4)}, p_min=0.6)
    assert cands == {"A": 0.1}                                # 지속성 미달 탈락


def test_cap_clip_redistribute():
    w = cap_clip_redistribute({"A": 0.5, "B": 0.3, "C": 0.2}, cap=0.4)
    assert w["A"] == pytest.approx(0.4)
    assert sum(w.values()) == pytest.approx(1.0)              # 초과분 비례 재배분
    all_capped = cap_clip_redistribute({"A": 0.6, "B": 0.6}, cap=0.4)
    assert all_capped == {"A": 0.4, "B": 0.4}                 # 잔여는 현금


# ── LC-G1 정적 불변식 검사 (Phase 3 DoD) ────────────────────────────────


MASTER = {
    "AAPL": ("FOREIGN_STOCK", None),
    "MSFT": ("FOREIGN_STOCK", None),
    "TQQQ": ("FOREIGN_ETF", "3.0"),
    "SPYX": ("FOREIGN_ETF", "1.0"),
    "NULE": ("FOREIGN_ETF", None),
}


def _strategy(n: int = 10):  # sleeve 1.0 × 1/10 = 10% ≤ 캡 12% (n=8은 12.5%로 위반)
    d = _valid_dict()
    d["entry_exit"]["entry"]["params"]["n"] = n
    return parse_strategy(d)


def test_static_check_passes_clean_strategy():
    inv = load_invariants()
    violations = static_invariant_check(
        _strategy(), inv,
        universe_symbols={"us_core": ["AAPL", "MSFT", "SPYX"]},
        whitelist={"us_core": {"AAPL", "MSFT", "SPYX"}},
        stock_master=MASTER,
    )
    assert violations == []


def test_static_check_rejects_violations():
    inv = load_invariants()
    violations = static_invariant_check(
        _strategy(n=4),  # 1.0/4 = 25% > 12% 캡 (INV-01)
        inv,
        universe_symbols={"us_core": ["AAPL", "TQQQ", "NULE", "GHOST"]},
        whitelist={"us_core": {"AAPL", "TQQQ", "NULE"}},  # GHOST는 밖 (INV-03)
        stock_master=MASTER,
    )
    text = "\n".join(violations)
    assert "INV-01" in text
    assert "GHOST" in text and "INV-03" in text
    assert "TQQQ rejected" in text                       # INV-11 레버리지
    assert "NULE indeterminate" in text                  # INV-11 null = 판정 불가


def test_static_check_fails_closed_without_master():
    inv = load_invariants()
    violations = static_invariant_check(
        _strategy(), inv,
        universe_symbols={"us_core": ["AAPL"]},
        whitelist={"us_core": {"AAPL"}},
        stock_master=None,   # 데이터 없음 = 통과가 아니라 검증 불가
    )
    assert any("INV-11" in v and "검증 불가" in v for v in violations)


# ── DoD: 러너(Phase 1)가 이 슬롯으로 end-to-end 백테스트 완주 ────────────


def test_slots_complete_end_to_end_backtest(registry, uptrend_store):
    from quantbot.backtest import judge, prereg, walkforward
    from conftest import LOOSE_GATES, LOW_COSTS, SMALL_METH

    grid = {"lookback": [3, 5], "skip": [1], "abs_filter": [True],
            "n": [1], "exit_buffer": [1.5]}
    signal = build_us_core_signal(
        {"lookback": 3, "skip": 1, "abs_filter": True, "n": 1, "exit_buffer": 1.5},
        cap=0.99,  # 단일 종목 보유 테스트라 캡 완화 (INV-01은 엔진 몫)
    )

    def signal_fn(view, params):
        fn = build_us_core_signal({**params, "n": 1, "exit_buffer": 1.5}, cap=0.99)
        return fn(view, None)

    rng = (uptrend_store.date(0), uptrend_store.date(len(uptrend_store) - 1))
    prereg.seal(registry, "slots-e2e", grid, rng, walkforward.folds_spec(SMALL_METH))
    res = judge.evaluate_oos(
        registry, uptrend_store, "slots-e2e", grid, rng,
        signal_fn, LOW_COSTS, SMALL_METH, LOOSE_GATES,
    )
    assert res.n_configs_tried > 0
    assert res.transition in ("paper", "rejected")  # 완주 — 판정까지 도달
    assert signal is not None