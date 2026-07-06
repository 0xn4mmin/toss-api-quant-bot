"""DoD: 사전등록 봉인 (BT-02) — 봉인 후 그리드 1칸 수정 시 러너 거부,
재봉인 거부, 봉인 아티팩트의 물리적 불변성(sqlite ABORT)."""

from __future__ import annotations

import copy
import sqlite3

import pytest

from quantbot.backtest import prereg, walkforward
from quantbot.backtest.walkforward import run_walkforward
from conftest import LOW_COSTS, SMALL_METH, momentum_top1

GRID = {"lookback": [3, 5, 8]}
SID = "prereg-strat"


def _range(store):
    return (store.date(0), store.date(len(store) - 1))


def test_runner_refuses_without_seal(registry, uptrend_store):
    with pytest.raises(prereg.PreregError, match="봉인이 없다"):
        run_walkforward(
            registry, uptrend_store, SID, GRID, _range(uptrend_store),
            momentum_top1, LOW_COSTS, SMALL_METH,
        )


def test_runner_refuses_after_one_cell_edit(registry, uptrend_store):
    """DoD 문항 그대로: 봉인 후 그리드 1칸 수정 → 해시 불일치 → 러너 거부."""
    rng = _range(uptrend_store)
    prereg.seal(registry, SID, GRID, rng, walkforward.folds_spec(SMALL_METH))

    edited = copy.deepcopy(GRID)
    edited["lookback"][1] = 6  # 5 → 6, 단 한 칸
    with pytest.raises(prereg.PreregError, match="해시 불일치"):
        run_walkforward(
            registry, uptrend_store, SID, edited, rng,
            momentum_top1, LOW_COSTS, SMALL_METH,
        )
    # 원본 그리드는 그대로 실행된다
    res = run_walkforward(
        registry, uptrend_store, SID, GRID, rng,
        momentum_top1, LOW_COSTS, SMALL_METH,
    )
    assert res.n_configs_tried > 0


def test_reseal_with_different_content_is_refused(registry, uptrend_store):
    """같은 id의 다른 내용 재봉인 거부 — 재탐색은 새 전략 id를 요구한다."""
    rng = _range(uptrend_store)
    sha1 = prereg.seal(registry, SID, GRID, rng, walkforward.folds_spec(SMALL_METH))
    # 멱등: 동일 내용은 같은 해시
    assert prereg.seal(registry, SID, GRID, rng, walkforward.folds_spec(SMALL_METH)) == sha1
    edited = {"lookback": [3, 5, 13]}
    with pytest.raises(prereg.PreregError, match="새 전략 id"):
        prereg.seal(registry, SID, edited, rng, walkforward.folds_spec(SMALL_METH))
    # 새 id로는 봉인 가능
    assert prereg.seal(registry, SID + "-v2", edited, rng,
                       walkforward.folds_spec(SMALL_METH)) != sha1


def test_sealed_artifact_is_physically_immutable(registry, uptrend_store):
    """봉인 수정은 코딩 규율이 아니라 sqlite 트리거가 거부한다 (Phase 0과 동일 방식)."""
    rng = _range(uptrend_store)
    prereg.seal(registry, SID, GRID, rng, walkforward.folds_spec(SMALL_METH))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        registry.connection.execute(
            "UPDATE artifacts SET sha256 = 'forged' WHERE kind = 'prereg'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        registry.connection.execute("DELETE FROM artifacts WHERE kind = 'prereg'")


def test_every_tried_config_is_appended_to_registry(registry, uptrend_store):
    """BT-G7 — n_configs_tried는 러너가 registry에 남긴 이벤트 수다."""
    rng = _range(uptrend_store)
    prereg.seal(registry, SID, GRID, rng, walkforward.folds_spec(SMALL_METH))
    res = run_walkforward(
        registry, uptrend_store, SID, GRID, rng,
        momentum_top1, LOW_COSTS, SMALL_METH,
    )
    events = [
        e for e in registry.events(walkforward.EVENT_CONFIG_TRIED)
        if e["payload"]["strategy_id"] == SID
    ]
    n_grid = len(walkforward.grid_configs(GRID))
    assert res.n_configs_tried == len(events) == n_grid * len(res.folds)
