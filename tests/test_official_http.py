"""공식 API HTTP 클라이언트 검사 — OAuth2·헤더·429/5xx/401 처리·그룹별 rate limit.

로컬 HTTP 서버에 진짜 urllib로 요청하므로 실자금 경로의 전송 계층 전체가 실코드다.
"""

from __future__ import annotations

import json

import pytest

from quantbot.adapter.official.http import (
    Credentials,
    OpenApiClient,
    OpenApiError,
    OpenApiHttpError,
    OpenApiPolicy,
    OpenApiRateLimited,
)
from conftest import make_openapi_policy


def test_token_issued_once_and_reused(official_server, official_client):
    official_client.get("/api/v1/prices", "MARKET_DATA", {"symbols": "AAPL"})
    official_client.get("/api/v1/exchange-rate", "MARKET_INFO")
    assert official_server.token_issues == 1  # 캐시된 토큰 재사용
    token_posts = [r for r in official_server.requests if r[0] == "POST"]
    assert len(token_posts) == 1


def test_bearer_and_account_headers(official_server, official_client):
    official_client.get("/api/v1/holdings", "ASSET", with_account=True)
    method, path, headers = official_server.requests[-1]
    assert (method, path) == ("GET", "/api/v1/holdings")
    assert headers.get("Authorization") == "Bearer tok-1"
    assert headers.get("X-Tossinvest-Account") == "1"
    # 계좌 목록(진입점)은 계좌 헤더 없이
    official_client.get("/api/v1/accounts", "ACCOUNT")
    _, _, headers2 = official_server.requests[-1]
    assert "X-Tossinvest-Account" not in headers2


def test_account_call_without_seq_is_refused(official_server):
    client = OpenApiClient(
        make_openapi_policy(official_server.base_url),
        Credentials("cid", "sec"),
        account_seq=None,
    )
    with pytest.raises(OpenApiError, match="account_seq"):
        client.get("/api/v1/holdings", "ASSET", with_account=True)


def test_429_waits_retry_after_then_succeeds(official_server, official_client):
    body = json.dumps({"error": {"code": "rate-limit-exceeded", "message": "slow down"}}).encode()
    official_server.fail_queue.append((429, {"Retry-After": "0"}, body))
    prices = official_client.get("/api/v1/prices", "MARKET_DATA", {"symbols": "AAPL"})
    assert prices["result"][0]["symbol"] == "AAPL"


def test_5xx_retries_then_succeeds(official_server, official_client):
    body = json.dumps({"error": {"code": "internal-error", "message": "oops"}}).encode()
    official_server.fail_queue.append((500, {}, body))
    result = official_client.get("/api/v1/exchange-rate", "MARKET_INFO")
    assert result["result"]["rate"] == "1352.30"


def test_4xx_is_immediate_typed_error_no_retry(official_server, official_client):
    body = json.dumps({"error": {
        "requestId": "01HXYZ", "code": "stock-not-found", "message": "없는 종목",
    }}).encode()
    official_server.fail_queue.append((404, {}, body))
    before = len([r for r in official_server.requests if r[0] == "GET"])
    with pytest.raises(OpenApiHttpError) as exc:
        official_client.get("/api/v1/prices", "MARKET_DATA", {"symbols": "XXXX"})
    assert exc.value.code == "stock-not-found"
    assert exc.value.request_id == "01HXYZ"
    assert not isinstance(exc.value, OpenApiRateLimited)
    after = len([r for r in official_server.requests if r[0] == "GET"])
    assert after - before == 1  # 재시도 없음


def test_401_triggers_single_token_refresh(official_server, official_client):
    official_client.get("/api/v1/exchange-rate", "MARKET_INFO")
    assert official_server.token_issues == 1
    body = json.dumps({"error": {"code": "expired-token", "message": "만료"}}).encode()
    official_server.fail_queue.append((401, {}, body))
    official_client.get("/api/v1/exchange-rate", "MARKET_INFO")
    assert official_server.token_issues == 2  # 강제 재발급 후 성공


def test_group_rate_limiter_is_per_group():
    """문서의 그룹별 TPS — ACCOUNT 1TPS는 1초 간격, 그룹 간에는 독립."""
    sleeps: list[float] = []
    # _rate_limit는 호출당 clock을 1~2회 읽는다: (elapsed 계산) + (last 갱신)
    clock_values = iter([0.0, 0.0, 0.3, 0.3])
    client = OpenApiClient(
        make_openapi_policy("http://127.0.0.1:1", group_tps={"ACCOUNT": 1.0, "ASSET": 5.0}),
        Credentials("cid", "sec"),
        sleep=sleeps.append,
        clock=lambda: next(clock_values),
    )
    client._rate_limit("ACCOUNT")   # t=0.0 — 대기 없음
    client._rate_limit("ASSET")     # 다른 그룹 — 대기 없음
    client._rate_limit("ACCOUNT")   # 0.3초 경과 — 0.7초 대기
    assert sleeps == [pytest.approx(0.7)]


def test_unregistered_group_is_refused(official_client):
    with pytest.raises(OpenApiError, match="Rate Limits Group"):
        official_client.get("/api/v1/prices", "NO_SUCH_GROUP")


def test_policy_loads_from_runtime_yaml():
    """그룹별 TPS 수치는 코드가 아니라 config/runtime.yaml에서 온다."""
    policy = OpenApiPolicy.from_runtime_yaml("config/runtime.yaml")
    assert policy.base_url == "https://openapi.tossinvest.com"
    assert policy.group_tps["ACCOUNT"] == 1.0    # 문서 Rate Limits 표
    assert policy.group_tps["MARKET_DATA"] == 10.0


def test_credentials_from_files(tmp_path):
    (tmp_path / "id").write_text("cid-x\n", encoding="utf-8")
    (tmp_path / "sec").write_text("sec-y\n", encoding="utf-8")
    creds = Credentials.from_files(tmp_path / "id", tmp_path / "sec")
    assert (creds.client_id, creds.client_secret) == ("cid-x", "sec-y")
