"""KR flows 일일 적재기 (IMPL-06) — 달력 시간이 표본을 만든다, 하루 늦으면 하루 준다.

매 거래일 장 마감 후 유니버스의 quote flows를 호출해 var/flows.db에
(종목, 거래일, 투자자별 순매수, 거래대금, 적재 시각)을 append한다.

구조로 강제되는 것:
- 중복 불가: (symbol, trade_date) UNIQUE — 이틀 연속 실행해도 신규 행만 추가된다.
- 행 불변: BEFORE UPDATE/DELETE 트리거가 ABORT (Phase 0 레지스트리와 동일 방식) —
  loaded_at(적재 시각)은 백테스트 look-ahead 필터의 근거라 사후 수정이 불가능해야 한다.
- 최초 실행은 제공되는 과거 이력을 최대 깊이로 백필하고 그 깊이를 registry에
  기록한다 — 3년 미만이면 KR 전략의 게이트 합격이 유보된다 (OF-01, paper-extended).
- 적재 실패는 삼키지 않는다: registry 경고 이벤트 + 로그 (텔레그램은 Phase 6 —
  그동안 cron 메일/로그가 임시 채널).

읽기 표면은 두 뷰가 같은 db를 본다 (§I6 데이터 경로 동일성):
rows()=최신 뷰(페이퍼·live), rows_as_of()=적재 시각 필터 뷰(백테스트).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from quantbot.adapter.contracts import SchemaDriftError
from quantbot.adapter.tossctl import flows as flows_surface
from quantbot.adapter.tossctl.proc import TossctlError, TossctlRunner
from quantbot.engine.registry import Registry

log = logging.getLogger("quantbot.collect.flows")

EVENT_BACKFILL = "flows_backfill"
EVENT_SNAPSHOT = "flows_snapshot"
EVENT_ERROR = "flows_snapshot_error"

_APPEND_ONLY_TRIGGER = """
    CREATE TRIGGER IF NOT EXISTS trg_flows_no_{op_lower}
    BEFORE {op} ON flows
    BEGIN
        SELECT RAISE(ABORT, 'flows store is append-only (IMPL-06): {op} rejected');
    END
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class FlowsStore:
    """flows.db — append 전용 일일 수급 스토어."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS flows ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " symbol TEXT NOT NULL,"
                " trade_date TEXT NOT NULL,"
                " foreign_net REAL NOT NULL,"
                " inst_net REAL NOT NULL,"
                " traded_value REAL NOT NULL,"
                " loaded_at TEXT NOT NULL,"
                " UNIQUE(symbol, trade_date))"
            )
            for op in ("UPDATE", "DELETE"):
                self._conn.execute(
                    _APPEND_ONLY_TRIGGER.format(op=op, op_lower=op.lower())
                )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "FlowsStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def append_rows(self, symbol: str, rows, loaded_at: str) -> int:
        """신규 (symbol, trade_date)만 추가하고 그 수를 반환 — 중복은 조용히 생략."""
        new = 0
        with self._conn:
            for r in rows:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO flows"
                    " (symbol, trade_date, foreign_net, inst_net, traded_value, loaded_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (symbol, r.date, r.foreign_net, r.inst_net, r.traded_value, loaded_at),
                )
                new += cur.rowcount
        return new

    def rows(self, symbol: str) -> list[tuple]:
        """최신 뷰 — 페이퍼·live 시그널 입력."""
        return list(self._conn.execute(
            "SELECT trade_date, foreign_net, inst_net, traded_value, loaded_at"
            " FROM flows WHERE symbol = ? ORDER BY trade_date",
            (symbol,),
        ))

    def rows_as_of(self, symbol: str, asof: str) -> list[tuple]:
        """백테스트 뷰 — 그 시점에 실제로 알 수 있었던 행만 (BT-D3: 적재 시각 필터)."""
        return list(self._conn.execute(
            "SELECT trade_date, foreign_net, inst_net, traded_value, loaded_at"
            " FROM flows WHERE symbol = ? AND trade_date <= ? AND loaded_at <= ?"
            " ORDER BY trade_date",
            (symbol, asof[:10], asof),
        ))

    def depth(self) -> tuple[str | None, str | None, int]:
        """(earliest, latest, distinct 거래일 수) — 백필 깊이의 근거."""
        row = self._conn.execute(
            "SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date) FROM flows"
        ).fetchone()
        return (row[0], row[1], row[2])

    def is_empty(self) -> bool:
        return self._conn.execute("SELECT COUNT(*) FROM flows").fetchone()[0] == 0


@dataclass
class SnapshotResult:
    new_rows_by_symbol: dict[str, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    backfilled: bool = False

    @property
    def ok(self) -> bool:
        return not self.failures


def snapshot(
    runner: TossctlRunner,
    registry: Registry,
    store: FlowsStore,
    symbols: list[str],
    *,
    now_fn: Callable[[], str] = _utcnow,
) -> SnapshotResult:
    """유니버스 전 종목의 flows를 적재한다. 최초 실행은 백필로 기록된다."""
    if not symbols:
        raise ValueError("적재할 종목이 없다 — 유니버스 입력 확인")
    first_run = store.is_empty()
    result = SnapshotResult()
    loaded_at = now_fn()
    for symbol in sorted(set(symbols)):
        try:
            data = flows_surface.flows(runner, symbol)
        except (TossctlError, SchemaDriftError) as e:
            result.failures[symbol] = f"{type(e).__name__}: {e}"
            log.warning("flows 적재 실패 %s: %s", symbol, e)
            registry.append_event(
                EVENT_ERROR, "warning",
                {"symbol": symbol, "error": str(e)[:500], "loaded_at": loaded_at},
            )
            continue
        result.new_rows_by_symbol[symbol] = store.append_rows(
            symbol, data.rows, loaded_at
        )
    earliest, latest, n_days = store.depth()
    registry.append_event(
        EVENT_SNAPSHOT, "info",
        {"loaded_at": loaded_at,
         "symbols": len(set(symbols)),
         "new_rows": sum(result.new_rows_by_symbol.values()),
         "failures": sorted(result.failures)},
    )
    if first_run and result.new_rows_by_symbol:
        # 백필 깊이 기록 — 3년 미만이면 KR 게이트 합격 유보의 근거 (OF-01)
        result.backfilled = True
        registry.append_event(
            EVENT_BACKFILL, "audit",
            {"earliest": earliest, "latest": latest, "distinct_days": n_days,
             "loaded_at": loaded_at},
        )
    if result.failures:
        log.warning(
            "flows 적재 부분 실패 %d건: %s — 텔레그램(Phase 6) 전까지 임시 채널은 "
            "이 로그와 cron 메일이다", len(result.failures), sorted(result.failures),
        )
    return result
