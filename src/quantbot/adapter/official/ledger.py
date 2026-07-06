"""공식 API 주문 이력 조회 표면 (ARCH-06 v1.1: Order History — 조회 전용).

주문 발행(POST)은 이 모듈과 무관하다 — Phase 4의 GATE 전용 표면(order.py)만
발행 경로를 갖는다. 이 표면은 대사(reconcile, RISK-06)와 아침 보고서의 근거다.
"""

from __future__ import annotations

from quantbot.adapter.official.contracts import (
    OrderRecord,
    PaginatedOrderResponse,
    call_api,
)
from quantbot.adapter.official.http import OpenApiClient


def orders_list(
    client: OpenApiClient, *, cursor: str | None = None
) -> PaginatedOrderResponse:
    params = {} if cursor is None else {"cursor": cursor}
    return call_api(
        client, "/api/v1/orders", "ORDER_HISTORY", PaginatedOrderResponse,
        params=params or None, with_account=True,
    )


def order_detail(client: OpenApiClient, order_id: str) -> OrderRecord:
    return call_api(
        client, f"/api/v1/orders/{order_id}", "ORDER_HISTORY", OrderRecord,
        with_account=True,
    )
