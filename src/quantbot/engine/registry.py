"""append-only 레지스트리 — sqlite (IMPL-04).

전략 생명주기 전이·백테스트 아티팩트·주문·시스템 이벤트를 기록한다.
append-only는 코딩 규율이 아니라 스키마로 강제한다: 전 테이블에
BEFORE UPDATE / BEFORE DELETE 트리거가 RAISE(ABORT)를 걸어 수정·삭제 SQL
자체가 실패한다. 상태 정정은 새 행 추가(이벤트 소싱)로만 가능하다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA_VERSION = 1

_TABLES: dict[str, str] = {
    "strategy_transitions": """
        CREATE TABLE IF NOT EXISTS strategy_transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id TEXT NOT NULL,
            from_state TEXT NOT NULL,
            to_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """,
    "artifacts": """
        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """,
    "orders": """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intent_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """,
    "events": """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            severity TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """,
}

_APPEND_ONLY_TRIGGER = """
    CREATE TRIGGER IF NOT EXISTS {name}
    BEFORE {op} ON {table}
    BEGIN
        SELECT RAISE(ABORT, 'registry is append-only (IMPL-04): {op} on {table} rejected');
    END
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Registry:
    """append 전용 표면 — update/delete 메서드는 존재하지 않고, SQL로 시도해도
    트리거가 ABORT한다."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            for table, ddl in _TABLES.items():
                self._conn.execute(ddl)
                for op in ("UPDATE", "DELETE"):
                    self._conn.execute(
                        _APPEND_ONLY_TRIGGER.format(
                            name=f"trg_{table}_no_{op.lower()}", op=op, table=table
                        )
                    )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " version INTEGER NOT NULL,"
                " created_at TEXT NOT NULL)"
            )
            for op in ("UPDATE", "DELETE"):
                self._conn.execute(
                    _APPEND_ONLY_TRIGGER.format(
                        name=f"trg_schema_version_no_{op.lower()}",
                        op=op,
                        table="schema_version",
                    )
                )
            cur = self._conn.execute("SELECT MAX(version) FROM schema_version")
            if cur.fetchone()[0] is None:
                self._conn.execute(
                    "INSERT INTO schema_version (version, created_at) VALUES (?, ?)",
                    (_SCHEMA_VERSION, _utcnow()),
                )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Registry":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── append 표면 (유일한 쓰기 경로) ──────────────────────────────

    def append_strategy_transition(
        self, strategy_id: str, from_state: str, to_state: str, reason: str
    ) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO strategy_transitions"
                " (strategy_id, from_state, to_state, reason, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (strategy_id, from_state, to_state, reason, _utcnow()),
            )
        return cur.lastrowid

    def append_artifact(
        self, strategy_id: str, kind: str, sha256: str, payload: dict
    ) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO artifacts (strategy_id, kind, sha256, payload, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (strategy_id, kind, sha256, json.dumps(payload, sort_keys=True), _utcnow()),
            )
        return cur.lastrowid

    def append_order(self, intent_hash: str, status: str, payload: dict) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO orders (intent_hash, status, payload, created_at)"
                " VALUES (?, ?, ?, ?)",
                (intent_hash, status, json.dumps(payload, sort_keys=True), _utcnow()),
            )
        return cur.lastrowid

    def append_event(self, kind: str, severity: str, payload: dict) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO events (kind, severity, payload, created_at)"
                " VALUES (?, ?, ?, ?)",
                (kind, severity, json.dumps(payload, sort_keys=True), _utcnow()),
            )
        return cur.lastrowid

    # ── 조회 표면 ──────────────────────────────────────────────────

    def rows(self, table: str) -> list[tuple]:
        if table not in (*_TABLES, "schema_version"):
            raise ValueError(f"알 수 없는 테이블: {table!r}")
        return list(self._conn.execute(f"SELECT * FROM {table} ORDER BY id"))

    def events(self, kind: str | None = None) -> list[dict]:
        """이벤트를 payload 파싱된 dict로 반환 (id 오름차순)."""
        sql = "SELECT id, kind, severity, payload, created_at FROM events"
        args: tuple = ()
        if kind is not None:
            sql += " WHERE kind = ?"
            args = (kind,)
        return [
            {"id": r[0], "kind": r[1], "severity": r[2],
             "payload": json.loads(r[3]), "created_at": r[4]}
            for r in self._conn.execute(sql + " ORDER BY id", args)
        ]

    def artifacts(
        self, strategy_id: str | None = None, kind: str | None = None
    ) -> list[dict]:
        """아티팩트를 payload 파싱된 dict로 반환 (id 오름차순)."""
        sql = "SELECT id, strategy_id, kind, sha256, payload, created_at FROM artifacts"
        conds, args = [], []
        if strategy_id is not None:
            conds.append("strategy_id = ?")
            args.append(strategy_id)
        if kind is not None:
            conds.append("kind = ?")
            args.append(kind)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return [
            {"id": r[0], "strategy_id": r[1], "kind": r[2], "sha256": r[3],
             "payload": json.loads(r[4]), "created_at": r[5]}
            for r in self._conn.execute(sql + " ORDER BY id", tuple(args))
        ]

    def transitions(self, strategy_id: str | None = None) -> list[dict]:
        """전략 생명주기 전이 이력 (id 오름차순)."""
        sql = ("SELECT id, strategy_id, from_state, to_state, reason, created_at"
               " FROM strategy_transitions")
        args: tuple = ()
        if strategy_id is not None:
            sql += " WHERE strategy_id = ?"
            args = (strategy_id,)
        return [
            {"id": r[0], "strategy_id": r[1], "from_state": r[2],
             "to_state": r[3], "reason": r[4], "created_at": r[5]}
            for r in self._conn.execute(sql + " ORDER BY id", args)
        ]

    @property
    def connection(self) -> sqlite3.Connection:
        """테스트·대사(reconcile)용 저수준 접근. 쓰기 시도는 트리거가 거부한다."""
        return self._conn
