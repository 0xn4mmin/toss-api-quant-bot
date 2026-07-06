"""ledger.* — 체결·주문 이력 조회 표면 (ARCH-06: orders list · transactions list).

조회 전용이다 — 주문 발행(order preview/place)은 이 모듈과 무관하며,
그 네임스페이스는 Phase 4의 GATE 전용 표면에만 존재한다. 이 모듈은 대사
(reconcile, RISK-06)와 아침 보고서의 근거 데이터를 제공한다.
"""

from __future__ import annotations

from quantbot.adapter.contracts import LedgerOrders, Transactions, call
from quantbot.adapter.proc import TossctlRunner


def orders_list(runner: TossctlRunner) -> LedgerOrders:
    return call(runner, ["orders", "list"], LedgerOrders)


def transactions_list(runner: TossctlRunner) -> Transactions:
    return call(runner, ["transactions", "list"], Transactions)
