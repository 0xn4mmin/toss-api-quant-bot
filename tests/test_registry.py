"""registry append-only 강제 검사 (IMPL-04) — UPDATE/DELETE는 sqlite가 ABORT."""

from __future__ import annotations

import sqlite3

import pytest

from quantbot.engine.registry import Registry, _TABLES


@pytest.fixture
def reg(tmp_path):
    with Registry(tmp_path / "registry.db") as r:
        yield r


def _populate(reg: Registry) -> None:
    reg.append_strategy_transition("s1", "draft", "backtest", "테스트 전이")
    reg.append_artifact("s1", "prereg", "ab" * 32, {"grid": "g"})
    reg.append_order("hash1", "previewed", {"symbol": "TEST"})
    reg.append_event("fail_safe_hold", "critical", {"cause": "test"})


def test_append_and_read_back(reg):
    _populate(reg)
    assert len(reg.rows("strategy_transitions")) == 1
    assert len(reg.rows("artifacts")) == 1
    assert len(reg.rows("orders")) == 1
    assert len(reg.rows("events")) == 1


@pytest.mark.parametrize("table", list(_TABLES) + ["schema_version"])
def test_update_is_aborted_by_sqlite(reg, table):
    """DoD: UPDATE 시도 시 sqlite가 ABORT한다 — 파이썬 코드가 아니라 스키마가 거부."""
    _populate(reg)
    col = {"strategy_transitions": "reason", "artifacts": "kind",
           "orders": "status", "events": "kind", "schema_version": "version"}[table]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        reg.connection.execute(f"UPDATE {table} SET {col} = 'tampered'")


@pytest.mark.parametrize("table", list(_TABLES) + ["schema_version"])
def test_delete_is_aborted_by_sqlite(reg, table):
    _populate(reg)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        reg.connection.execute(f"DELETE FROM {table}")


def test_abort_survives_raw_reconnect(reg, tmp_path):
    """Registry 클래스를 거치지 않은 생 연결에서도 트리거가 거부한다 —
    append-only가 코드 규율이 아니라 DB 파일에 새겨진 속성임을 확인."""
    _populate(reg)
    raw = sqlite3.connect(tmp_path / "registry.db")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            raw.execute("UPDATE events SET severity = 'info'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            raw.execute("DELETE FROM orders")
    finally:
        raw.close()


def test_every_table_has_both_triggers(reg):
    """구조 검사: 사용자 테이블 전부에 no_update·no_delete 트리거가 존재한다.
    새 테이블을 추가하면서 트리거를 빼먹는 실수를 스키마 수준에서 잡는다."""
    tables = {
        r[0]
        for r in reg.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%'"
        )
    }
    triggers = {
        r[0]
        for r in reg.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    for table in tables:
        assert f"trg_{table}_no_update" in triggers, f"{table}: UPDATE 트리거 없음"
        assert f"trg_{table}_no_delete" in triggers, f"{table}: DELETE 트리거 없음"


def test_registry_surface_has_no_mutators(reg):
    """Registry 공개 표면에 update/delete류 메서드가 존재하지 않는다."""
    public = {m for m in dir(reg) if not m.startswith("_")}
    mutating = {m for m in public if any(w in m for w in ("update", "delete", "remove", "set_"))}
    assert not mutating, f"변경 메서드 발견: {mutating}"
