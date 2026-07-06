"""기동 대사 (RISK-06) — "봇이 모르는 체결"이 있으면 hold 상태로 기동한다.

새벽 프로세스 사망 후의 유령 주문이 조용히 넘어가는 것을 막는 장치: 모든 기동 시
브로커의 주문 이력(공식 API GET /orders)을 로컬 registry 주문 로그와 대조한다.
미대사 항목 존재 = fail-safe hold로 기동 — 해제는 사람의 Tier-2 승인.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from quantbot.adapter.official.contracts import OrderRecord
from quantbot.engine.registry import Registry
from quantbot.engine.watcher import Watcher

EVENT_RECONCILE = "reconcile"


@dataclass(frozen=True)
class ReconcileResult:
    known: int
    ghosts: tuple[str, ...]   # registry에 없는 브로커 주문 id

    @property
    def clean(self) -> bool:
        return not self.ghosts


def known_order_ids(registry: Registry) -> set[str]:
    """registry 주문 로그가 아는 브로커 주문 id (live 체결이 기록한 orderId)."""
    ids: set[str] = set()
    for row in registry.rows("orders"):
        # payload는 JSON 문자열 (테이블 스키마: id, intent_hash, status, payload, created_at)
        payload = json.loads(row[3])
        oid = payload.get("orderId")
        if oid:
            ids.add(str(oid))
    return ids


def reconcile_startup(
    registry: Registry,
    broker_orders: Sequence[OrderRecord],
    watcher: Watcher,
) -> ReconcileResult:
    """기동 시 1회 — 유령 체결이 있으면 hold로 기동한다."""
    known = known_order_ids(registry)
    ghosts = tuple(sorted(
        o.orderId for o in broker_orders if o.orderId not in known
    ))
    result = ReconcileResult(known=len(known), ghosts=ghosts)
    registry.append_event(EVENT_RECONCILE, "audit" if result.clean else "critical", {
        "broker_orders": len(broker_orders),
        "known": result.known,
        "ghosts": list(ghosts),
    })
    if ghosts:
        watcher.hold("ghost_orders", {"ghosts": list(ghosts)})
    return result
