"""공식 API 계좌·자산 표면 (ARCH-06 v1.1: Account · Asset). 계좌 헤더 필수."""

from __future__ import annotations

from quantbot.adapter.official.contracts import Account, HoldingsOverview, call_api
from quantbot.adapter.official.http import OpenApiClient


def accounts(client: OpenApiClient) -> list[Account]:
    """계좌 목록 — accountSeq를 얻는 진입점이므로 계좌 헤더 없이 호출한다."""
    return call_api(client, "/api/v1/accounts", "ACCOUNT", Account, many=True)


def holdings(client: OpenApiClient) -> HoldingsOverview:
    return call_api(
        client, "/api/v1/holdings", "ASSET", HoldingsOverview, with_account=True,
    )
