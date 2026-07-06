"""텔레그램 Bot API 클라이언트 (ARCH-05, ARCH-08) — urllib 롱폴링.

urllib.request를 import할 수 있는 두 곳 중 하나 (아키텍처 테스트 — 나머지는
공식 API 클라이언트). 전송 계층은 주입 가능(transport) — 테스트는 로컬 서버로
실경로를 검증한다. 이 모듈은 전송만 안다 — 명령 의미는 router가, 안전 게이트는
엔진이 갖는다 (인터페이스가 무엇이든 게이트는 단일하다, §8).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

API_BASE = "https://api.telegram.org"


class TelegramError(Exception):
    pass


def _urllib_transport(url: str, form: dict, timeout_s: float) -> dict:
    data = urllib.parse.urlencode(form).encode("ascii")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except ValueError:
            raise TelegramError(f"HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise TelegramError(f"연결 실패: {e.reason}") from e


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        api_base: str = API_BASE,
        timeout_s: float,
        poll_timeout_s: float,
        transport: Callable[[str, dict, float], dict] | None = None,
    ) -> None:
        if not token:
            raise TelegramError("빈 토큰")
        self._token = token
        self._base = api_base.rstrip("/")
        self._timeout = timeout_s
        self._poll_timeout = poll_timeout_s
        self._transport = transport or _urllib_transport

    @classmethod
    def from_token_file(cls, path: str | Path, **kw) -> "TelegramClient":
        try:
            token = Path(path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise TelegramError(f"토큰 파일을 읽을 수 없다: {e}") from e
        return cls(token, **kw)

    def _call(self, method: str, form: dict) -> object:
        url = f"{self._base}/bot{self._token}/{method}"
        body = self._transport(url, form, self._timeout + self._poll_timeout)
        if not isinstance(body, dict) or not body.get("ok"):
            raise TelegramError(f"{method} 실패: {body}")
        return body.get("result")

    def get_updates(self, offset: int | None = None) -> list[dict]:
        form: dict = {"timeout": int(self._poll_timeout)}
        if offset is not None:
            form["offset"] = offset
        result = self._call("getUpdates", form)
        return list(result) if isinstance(result, list) else []

    def send_message(self, chat_id: int | str, text: str) -> None:
        self._call("sendMessage", {"chat_id": chat_id, "text": text})


def poll_once(client: TelegramClient, handle: Callable[[int, str], str | None],
              offset: int | None) -> int | None:
    """getUpdates 1회 소비 — 각 메시지를 handle(chat_id, text)에 넘기고 응답 발신.

    반환값은 다음 offset. 루프는 조립 루트(cli)가 돈다.
    """
    for update in client.get_updates(offset):
        offset = update["update_id"] + 1
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = msg.get("text")
        if chat_id is None or not text:
            continue
        reply = handle(chat_id, text)
        if reply:
            client.send_message(chat_id, reply)
    return offset
