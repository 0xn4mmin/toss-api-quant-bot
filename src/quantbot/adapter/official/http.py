"""공식 Open API HTTP 클라이언트 (IMPL-03 v1.1) — urllib 기반, 조회(GET) 전용.

프로젝트에서 urllib.request를 import할 수 있는 곳은 이 모듈(과 텔레그램 인터페이스)
뿐이다 (IMPL-02 장치 2 v1.1).

구조로 강제되는 것:
- 공개 표면은 get() 하나 — 범용 POST 메서드가 존재하지 않는다. 주문(POST)은
  Phase 4의 GATE 전용 표면(order.py)이 자기 전용 경로를 갖고 도입된다.
  토큰 발급 POST는 내부 전용이며 경로가 /oauth2/token 상수로 고정돼 있다.
- 그룹별 rate limit (문서: ACCOUNT 1TPS 등) — 그룹 간 독립, 호출 간 최소 간격.
- 429는 Retry-After 초만큼 대기 후 재시도(문서 권장), 5xx·타임아웃은 지수 백오프,
  그 외 4xx는 재시도 없이 에러 envelope을 typed 예외로 상향.
- 자격증명은 파일 주입(var/secrets/ — git 금지, ISO 격리) 또는 명시 주입.

수치(타임아웃·재시도·그룹별 TPS)는 config/runtime.yaml adapter.official 섹션 주입.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from quantbot import _yaml

TOKEN_PATH = "/oauth2/token"
ACCOUNT_HEADER = "X-Tossinvest-Account"
_TOKEN_EXPIRY_MARGIN_S = 30.0  # 만료 직전 재발급 여유


class OpenApiError(Exception):
    """공식 API 클라이언트 계층의 공통 예외."""


class OpenApiAuthError(OpenApiError):
    pass


class OpenApiBadJson(OpenApiError):
    """응답 본문이 JSON이 아니다 — 스키마 이전 단계의 실패."""


class OpenApiHttpError(OpenApiError):
    """비 2xx 응답 — 문서의 에러 envelope을 typed으로 상향."""

    def __init__(self, status: int, code: str, message: str, request_id: str | None) -> None:
        super().__init__(f"HTTP {status} [{code}] {message} (requestId={request_id})")
        self.status = status
        self.code = code
        self.request_id = request_id


class OpenApiRateLimited(OpenApiHttpError):
    """429 — Retry-After 포함."""

    def __init__(self, status: int, code: str, message: str, request_id: str | None,
                 retry_after_s: float) -> None:
        super().__init__(status, code, message, request_id)
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str

    @classmethod
    def from_files(cls, client_id_path: str | Path, client_secret_path: str | Path) -> "Credentials":
        try:
            cid = Path(client_id_path).read_text(encoding="utf-8").strip()
            sec = Path(client_secret_path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise OpenApiAuthError(f"자격증명 파일을 읽을 수 없다: {e}") from e
        if not cid or not sec:
            raise OpenApiAuthError("자격증명 파일이 비어 있다")
        return cls(client_id=cid, client_secret=sec)


@dataclass(frozen=True)
class OpenApiPolicy:
    base_url: str
    timeout_s: float
    max_retries: int
    backoff_base_s: float
    group_tps: dict[str, float]   # Rate Limits Group → 초당 허용 횟수 (문서 표)

    @classmethod
    def from_config(cls, cfg: dict) -> "OpenApiPolicy":
        base_url = cfg.get("base_url")
        if not isinstance(base_url, str) or not base_url.startswith("http"):
            raise OpenApiError(f"adapter.official.base_url: URL 필요: {base_url!r}")
        vals = {}
        for key in ("timeout_s", "max_retries", "backoff_base_s"):
            v = cfg.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise OpenApiError(f"adapter.official.{key}: 0 이상 숫자 필요: {v!r}")
            vals[key] = v
        tps = cfg.get("rate_limits_tps")
        if not isinstance(tps, dict) or not tps:
            raise OpenApiError("adapter.official.rate_limits_tps: 그룹별 TPS 필요")
        group_tps = {}
        for g, v in tps.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise OpenApiError(f"rate_limits_tps.{g}: 양수 필요: {v!r}")
            group_tps[str(g)] = float(v)
        return cls(
            base_url=base_url.rstrip("/"),
            timeout_s=float(vals["timeout_s"]),
            max_retries=int(vals["max_retries"]),
            backoff_base_s=float(vals["backoff_base_s"]),
            group_tps=group_tps,
        )

    @classmethod
    def from_runtime_yaml(cls, path: str | Path) -> "OpenApiPolicy":
        data = _yaml.load_file(str(path))
        adapter = data.get("adapter")
        if not isinstance(adapter, dict) or not isinstance(adapter.get("official"), dict):
            raise OpenApiError(f"{path}: adapter.official 섹션이 없다")
        return cls.from_config(adapter["official"])


def _parse_error(status: int, body: bytes, headers) -> OpenApiHttpError:
    code, message, request_id = "unknown", "", None
    try:
        env = json.loads(body.decode("utf-8"))
        err = env.get("error", {}) if isinstance(env, dict) else {}
        code = err.get("code", code)
        message = err.get("message", "")
        request_id = err.get("requestId")
    except (ValueError, UnicodeDecodeError):
        message = body.decode("utf-8", "replace")[:200]
    if status == 429:
        retry_after = float(headers.get("Retry-After", "1") or "1")
        return OpenApiRateLimited(status, code, message, request_id, retry_after)
    return OpenApiHttpError(status, code, message, request_id)


class OpenApiClient:
    """공식 API 호출의 유일한 관문 — 조회(GET) 표면만 공개한다."""

    def __init__(
        self,
        policy: OpenApiPolicy,
        credentials: Credentials,
        *,
        account_seq: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._credentials = credentials
        self._account_seq = account_seq
        self._sleep = sleep
        self._clock = clock
        self._last_call_by_group: dict[str, float] = {}
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # ── 그룹별 rate limit (문서 §Rate Limits) ─────────────────────────

    def _rate_limit(self, group: str) -> None:
        tps = self._policy.group_tps.get(group)
        if tps is None:
            raise OpenApiError(f"미등록 Rate Limits Group: {group!r} — runtime.yaml에 추가 필요")
        min_interval = 1.0 / tps
        last = self._last_call_by_group.get(group)
        if last is not None:
            wait = min_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_call_by_group[group] = self._clock()

    # ── OAuth2 토큰 (내부 전용 POST — 경로 상수 고정) ──────────────────

    def _ensure_token(self) -> str:
        if self._token is not None and self._clock() < self._token_expires_at:
            return self._token
        self._rate_limit("AUTH")
        form = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self._credentials.client_id,
            "client_secret": self._credentials.client_secret,
        }).encode("ascii")
        req = urllib.request.Request(
            self._policy.base_url + TOKEN_PATH,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        status, headers, body = self._transport(req)
        if status != 200:
            raise OpenApiAuthError(f"토큰 발급 실패: {_parse_error(status, body, headers)}")
        try:
            tok = json.loads(body.decode("utf-8"))
            access, expires_in = tok["access_token"], float(tok["expires_in"])
        except (ValueError, KeyError, TypeError) as e:
            raise OpenApiAuthError(f"토큰 응답 형식 오류: {e}") from e
        self._token = access
        self._token_expires_at = self._clock() + expires_in - _TOKEN_EXPIRY_MARGIN_S
        return access

    def _transport(self, req: urllib.request.Request) -> tuple[int, dict, bytes]:
        """urllib 실행 — 상태/헤더/본문으로 정규화. 테스트는 로컬 HTTP 서버로 검증."""
        try:
            with urllib.request.urlopen(req, timeout=self._policy.timeout_s) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), e.read()
        except urllib.error.URLError as e:
            raise OpenApiError(f"연결 실패: {e.reason}") from e

    # ── 공개 표면: GET 하나뿐 ─────────────────────────────────────────

    def get(
        self,
        path: str,
        group: str,
        params: dict[str, str] | None = None,
        *,
        with_account: bool = False,
    ) -> object:
        """GET {base_url}{path} — 파싱된 JSON 본문을 반환한다."""
        if not path.startswith("/"):
            raise OpenApiError(f"path는 '/'로 시작해야 한다: {path!r}")
        url = self._policy.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(sorted(params.items()))
        attempts = 1 + self._policy.max_retries
        last_exc: OpenApiError | None = None
        for attempt in range(attempts):
            if attempt > 0 and not isinstance(last_exc, OpenApiRateLimited):
                self._sleep(self._policy.backoff_base_s * (2 ** (attempt - 1)))
            headers = {"Authorization": f"Bearer {self._ensure_token()}"}
            if with_account:
                if not self._account_seq:
                    raise OpenApiError("계좌 API 호출에는 account_seq가 필요하다")
                headers[ACCOUNT_HEADER] = self._account_seq
            self._rate_limit(group)
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                status, resp_headers, body = self._transport(req)
            except OpenApiError as e:
                last_exc = e
                continue
            if status == 200:
                try:
                    return json.loads(body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as e:
                    raise OpenApiBadJson(f"GET {path}: 본문이 JSON이 아니다: {e}") from e
            err = _parse_error(status, body, resp_headers)
            if isinstance(err, OpenApiRateLimited):
                last_exc = err
                self._sleep(err.retry_after_s)  # 문서 권장: Retry-After 만큼 대기
                continue
            if status in (401,) and attempt == 0:
                # 토큰 만료(expired-token 등) — 1회 강제 재발급 후 재시도
                self._token = None
                last_exc = err
                continue
            if 500 <= status < 600:
                last_exc = err
                continue
            raise err  # 그 외 4xx — 재시도 없이 즉시 상향
        assert last_exc is not None
        raise last_exc
