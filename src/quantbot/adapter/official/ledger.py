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


ORDER_STATUS_GROUPS = ("OPEN", "CLOSED")  # 명세: 필수 라이프사이클 그룹 필터


def orders_list(
    client: OpenApiClient,
    status: str,
    *,
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> PaginatedOrderResponse:
    """주문 목록 — status는 필수 (2026-07-07 실측 확정: 없으면 400 invalid-request).

    OPEN=진행 중(전량 반환, cursor/limit 무시), CLOSED=종료(페이지네이션).
    from/to는 orderedAt 기준 KST 일자.
    """
    if status not in ORDER_STATUS_GROUPS:
        raise ValueError(f"status ∈ {ORDER_STATUS_GROUPS}: {status!r}")
    params: dict[str, str] = {"status": status}
    if symbol is not None:
        params["symbol"] = symbol
    if date_from is not None:
        params["from"] = date_from
    if date_to is not None:
        params["to"] = date_to
    if cursor is not None:
        params["cursor"] = cursor
    if limit is not None:
        params["limit"] = str(limit)
    return call_api(
        client, "/api/v1/orders", "ORDER_HISTORY", PaginatedOrderResponse,
        params=params, with_account=True,
    )


def order_detail(client: OpenApiClient, order_id: str) -> OrderRecord:
    return call_api(
        client, f"/api/v1/orders/{order_id}", "ORDER_HISTORY", OrderRecord,
        with_account=True,
    )
