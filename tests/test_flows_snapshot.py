"""Phase 2.5 DoD — 백필 깊이 registry 기록 / 이틀 연속 실행 중복 없음 / 실패 경고."""

from __future__ import annotations

import json
import sqlite3

import pytest

from quantbot.adapter.tossctl.proc import TossctlRunner
from quantbot.collect.flows_snapshot import (
    EVENT_BACKFILL,
    EVENT_ERROR,
    EVENT_SNAPSHOT,
    FlowsStore,
    snapshot,
)
from conftest import make_run_policy


def write_flows_fixture(fx_dir, symbol: str, days: list[str]) -> None:
    fx_dir.mkdir(parents=True, exist_ok=True)
    obj = {
        "symbol": symbol,
        "rows": [
            {"date": d, "foreign_net": 1000.0 + i, "inst_net": -100.0 - i,
             "traded_value": 50000.0}
            for i, d in enumerate(days)
        ],
    }
    (fx_dir / f"quote_flows_{symbol}.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8"
    )


DAYS5 = ["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"]


@pytest.fixture
def flows_env(tmp_path, monkeypatch):
    fx = tmp_path / "fx"
    write_flows_fixture(fx, "005930", DAYS5)
    write_flows_fixture(fx, "000660", DAYS5)
    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(fx))
    return fx


@pytest.fixture
def store(tmp_path):
    with FlowsStore(tmp_path / "flows.db") as s:
        yield s


def _runner():
    return TossctlRunner(make_run_policy())


def test_backfill_depth_is_recorded_in_registry(flows_env, registry, store):
    """DoD: 최초 실행 = 백필, 그 깊이가 registry에 기록된다 (OF-01의 근거)."""
    result = snapshot(_runner(), registry, store, ["005930", "000660"],
                      now_fn=lambda: "2026-07-06T07:10:00+00:00")
    assert result.ok and result.backfilled
    assert result.new_rows_by_symbol == {"005930": 5, "000660": 5}
    backfills = registry.events(EVENT_BACKFILL)
    assert len(backfills) == 1
    p = backfills[0]["payload"]
    assert (p["earliest"], p["latest"], p["distinct_days"]) == \
        ("2026-06-29", "2026-07-03", 5)
    assert len(registry.events(EVENT_SNAPSHOT)) == 1


def test_second_run_appends_nothing_and_no_second_backfill(flows_env, registry, store):
    """DoD: 이틀 연속 실행 시 중복 없이 append — 신규 0행, 백필 이벤트는 1회뿐."""
    snapshot(_runner(), registry, store, ["005930", "000660"])
    before = store.connection.execute("SELECT COUNT(*) FROM flows").fetchone()[0]
    result2 = snapshot(_runner(), registry, store, ["005930", "000660"])
    assert result2.backfilled is False
    assert sum(result2.new_rows_by_symbol.values()) == 0
    after = store.connection.execute("SELECT COUNT(*) FROM flows").fetchone()[0]
    assert before == after == 10
    assert len(registry.events(EVENT_BACKFILL)) == 1
    assert len(registry.events(EVENT_SNAPSHOT)) == 2


def test_new_trading_day_appends_only_new_rows(flows_env, registry, store):
    snapshot(_runner(), registry, store, ["005930"])
    write_flows_fixture(flows_env, "005930", DAYS5 + ["2026-07-06"])
    result = snapshot(_runner(), registry, store, ["005930"])
    assert result.new_rows_by_symbol == {"005930": 1}
    assert [r[0] for r in store.rows("005930")][-1] == "2026-07-06"


def test_failure_is_warned_and_partial_load_continues(flows_env, registry, store):
    """DoD: 적재 실패 시 경고(registry 이벤트 + 로그) — 나머지 종목은 계속 적재."""
    result = snapshot(_runner(), registry, store, ["005930", "NOFIX"])
    assert not result.ok
    assert "NOFIX" in result.failures
    assert result.new_rows_by_symbol == {"005930": 5}  # 부분 성공
    errors = registry.events(EVENT_ERROR)
    assert len(errors) == 1 and errors[0]["payload"]["symbol"] == "NOFIX"
    assert errors[0]["severity"] == "warning"


def test_store_rows_are_physically_immutable(flows_env, registry, store):
    """loaded_at은 look-ahead의 근거 — 사후 수정은 sqlite가 ABORT."""
    snapshot(_runner(), registry, store, ["005930"])
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute("UPDATE flows SET loaded_at = '1970-01-01'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute("DELETE FROM flows")


def test_as_of_view_filters_by_loaded_at_and_trade_date(flows_env, registry, store):
    """백테스트 뷰(BT-D3): 그 시점에 실제로 알 수 있었던 행만 보인다."""
    snapshot(_runner(), registry, store, ["005930"],
             now_fn=lambda: "2026-07-06T07:10:00+00:00")
    write_flows_fixture(flows_env, "005930", DAYS5 + ["2026-07-06"])
    snapshot(_runner(), registry, store, ["005930"],
             now_fn=lambda: "2026-07-07T07:10:00+00:00")
    assert len(store.rows("005930")) == 6                      # 최신 뷰: 전부
    # 7/6 저녁 시점: 7/6 거래일 행은 아직 적재 전(7/7 적재) — 보이면 look-ahead
    asof = store.rows_as_of("005930", "2026-07-06T23:00:00+00:00")
    assert [r[0] for r in asof] == DAYS5
    # 백필(7/6 적재) 이전 시점에는 아무것도 "알 수 있었던 것"이 없다
    assert store.rows_as_of("005930", "2026-07-01T23:00:00+00:00") == []
    # 거래일 필터: 적재는 됐지만 미래 거래일인 행은 잘린다
    write_flows_fixture(flows_env, "005930", DAYS5 + ["2026-07-06", "2026-07-10"])
    snapshot(_runner(), registry, store, ["005930"],
             now_fn=lambda: "2026-07-07T09:00:00+00:00")
    asof3 = store.rows_as_of("005930", "2026-07-08T23:00:00+00:00")
    assert [r[0] for r in asof3] == DAYS5 + ["2026-07-06"]     # 7/10 행은 제외


def test_empty_universe_is_refused(registry, store):
    with pytest.raises(ValueError):
        snapshot(_runner(), registry, store, [])
