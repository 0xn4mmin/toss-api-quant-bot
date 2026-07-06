"""2등급 입력 라우팅 (ARCH §8, TG-05) — 불변식 변경 명령은 존재하지 않는다.

라우팅 표가 곧 권한 모델이다: TIER-1(조회·안전 방향)은 즉시, TIER-2(비가역)는
preview 회신 → 소유자가 일회용 confirm token(5분 TTL·단일 사용)을 되보내야
집행된다. 표는 모듈 상수 — 스냅샷 테스트가 행 추가/변경을 잡는다. 불변식을
바꾸는 명령은 이 표에 없고, 추가하는 순간 스냅샷 테스트가 빨강이 된다 (§4).

핸들러는 조립 루트(cli)가 주입한다 — 인터페이스는 엔진 명령 큐·보고 조회만
알고, 어댑터·전략 계층을 직접 만질 수 없다 (아키텍처 테스트가 강제).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

TIER1 = "tier1"
TIER2 = "tier2"

# ── 라우팅 표 (ARCH §8) — 이 표가 전부다. 불변식 변경 명령은 존재하지 않는다.
ROUTING: dict[str, str] = {
    "/status": TIER1,
    "/positions": TIER1,
    "/pnl": TIER1,
    "/report": TIER1,
    "/strategy": TIER1,      # list · diff — 조회
    "/switch": TIER1,        # 자동 승인 파이프라인 통과 시에만 즉시, 아니면 Tier-2 격상
    "/pause": TIER1,         # 안전한 방향은 마찰 없이
    "/order": TIER2,         # 수동 주문 — preview → confirm token
    "/resume": TIER2,        # fail-safe hold 해제 (§9)
    "/cb-release": TIER2,    # 서킷브레이커 해제
    "/promote": TIER2,       # 게이트 수동 override — preview에 미검증 경고 명시
    "/confirm": TIER2,       # Tier-2 집행 — token 소비
}


class RouterError(Exception):
    pass


@dataclass
class PendingAction:
    command: str
    args: str
    issued_at: float


@dataclass
class TokenStore:
    """confirm token — 5분 TTL·단일 사용 (TG-05). 수치는 생성자 주입."""

    ttl_s: float
    clock: Callable[[], float] = time.monotonic
    _pending: dict[str, PendingAction] = field(default_factory=dict)

    def issue(self, command: str, args: str) -> str:
        token = secrets.token_hex(4)
        self._pending[token] = PendingAction(command, args, self.clock())
        return token

    def consume(self, token: str) -> PendingAction:
        action = self._pending.pop(token, None)  # pop = 단일 사용
        if action is None:
            raise RouterError("무효 token — 이미 사용됐거나 발급된 적 없다")
        if self.clock() - action.issued_at > self.ttl_s:
            raise RouterError("token 만료(TTL) — preview부터 다시")
        return action


Handler = Callable[[str], str]           # args → 응답 텍스트 (TIER-1 즉시)
PreviewFn = Callable[[str], str]         # args → preview 텍스트 (TIER-2 1단)
ExecuteFn = Callable[[str], str]         # args → 집행 결과 (TIER-2 2단, token 후)


@dataclass
class Router:
    owner_chat_id: int
    tier1: Mapping[str, Handler]
    tier2_preview: Mapping[str, PreviewFn]
    tier2_execute: Mapping[str, ExecuteFn]
    tokens: TokenStore

    def handle(self, chat_id: int, text: str) -> str:
        if chat_id != self.owner_chat_id:
            return "권한 없음"  # 소유자 1인 시스템 — 정보 노출 없이 거부
        word, _, args = text.strip().partition(" ")
        tier = ROUTING.get(word)
        if tier is None:
            return f"알 수 없는 명령: {word} (불변식 변경 명령은 존재하지 않는다 — §4)"

        if word == "/confirm":
            try:
                action = self.tokens.consume(args.strip())
            except RouterError as e:
                return str(e)
            return self.tier2_execute[action.command](action.args)

        if tier == TIER1:
            handler = self.tier1.get(word)
            if handler is None:
                return f"{word}: 핸들러 미구성"
            return handler(args)

        preview_fn = self.tier2_preview.get(word)
        if preview_fn is None:
            return f"{word}: preview 미구성"
        preview = preview_fn(args)
        token = self.tokens.issue(word, args)
        return (
            f"{preview}\n\n비가역 행동이다 — 집행하려면 5분 내에:\n/confirm {token}"
        )
