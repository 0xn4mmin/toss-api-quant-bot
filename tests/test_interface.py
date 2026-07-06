"""Phase 6 DoD — 라우팅 표 전 행 / /resume은 preview·token 없이 재개 불가 /
불변식 변경 명령 부재(스냅샷) / 보고서에 시그널 근거·비용·미검증 표기."""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from quantbot.engine import caps
from quantbot.engine.watcher import Watcher, WatcherConfig
from quantbot.interface.reports import morning_report
from quantbot.interface.router import ROUTING, Router, RouterError, TokenStore
from quantbot.interface.telegram import TelegramClient, poll_once

OWNER = 777


def _router(clock=None, watcher=None, ttl=300.0):
    executed = []
    tier2_execute = {
        "/resume": lambda args: (
            watcher.release_hold(confirmed_by_tier2=True, detail=args or "/resume")
            or "재개 완료"
        ) if watcher else executed.append(("/resume", args)) or "재개 완료",
        "/order": lambda args: executed.append(("/order", args)) or "주문 집행(페이퍼)",
        "/cb-release": lambda args: "CB 해제",
        "/promote": lambda args: "강제 승격",
    }
    router = Router(
        owner_chat_id=OWNER,
        tier1={
            "/status": lambda a: "정상",
            "/positions": lambda a: "보유 없음",
            "/pnl": lambda a: "+0",
            "/report": lambda a: "보고서",
            "/strategy": lambda a: f"strategy {a}",
            "/switch": lambda a: "자동 승인 통과 — 전환",
            "/pause": lambda a: "일시 중단",
        },
        tier2_preview={
            "/order": lambda a: f"preview: {a} 예상 체결·수수료",
            "/resume": lambda a: "preview: 헬스체크 결과·재개 시 예정 동작",
            "/cb-release": lambda a: "preview: 서킷브레이커 상태",
            "/promote": lambda a: "preview: ⚠ 미검증 — 백테스트 미충족 항목",
        },
        tier2_execute=tier2_execute,
        tokens=TokenStore(ttl_s=ttl, clock=clock or (lambda: 0.0)),
    )
    return router, executed


# ── DoD: 라우팅 표 전 행 + 불변식 변경 명령 부재 (스냅샷 고정) ───────────


def test_routing_table_snapshot_is_exact():
    """표 자체를 고정한다 — 행 추가(예: 불변식 변경 명령)는 이 테스트를 깬다 (§4)."""
    assert ROUTING == {
        "/status": "tier1", "/positions": "tier1", "/pnl": "tier1",
        "/report": "tier1", "/strategy": "tier1", "/switch": "tier1",
        "/pause": "tier1",
        "/order": "tier2", "/resume": "tier2", "/cb-release": "tier2",
        "/promote": "tier2", "/confirm": "tier2",
    }
    assert not any("invariant" in c or "불변식" in c for c in ROUTING)


def test_every_routing_row_behaves_per_tier():
    """DoD: Tier-1은 즉시 응답, Tier-2는 preview+token 요구 — 전 행 검사."""
    router, _ = _router()
    for cmd, tier in ROUTING.items():
        if cmd == "/confirm":
            continue
        reply = router.handle(OWNER, cmd + " x")
        if tier == "tier1":
            assert "/confirm" not in reply, cmd     # 즉시 — token 없음
        else:
            assert "preview" in reply and "/confirm " in reply, cmd


def test_unknown_and_unauthorized():
    router, _ = _router()
    assert "존재하지 않는다" in router.handle(OWNER, "/set-invariant mdd=99")
    assert router.handle(999, "/status") == "권한 없음"


# ── DoD: /resume은 preview·token 없이 재개 불가 ─────────────────────────


def _held_watcher(registry):
    state = caps.CapsState()
    state.start_day(1.0)
    w = Watcher(
        registry=registry, caps_state=state,
        config=WatcherConfig(heartbeat_timeout_s=90, max_consecutive_errors=3),
        positions=dict, escalate=lambda r, c: None,
    )
    w.hold("test")
    return w, state


def test_resume_requires_preview_then_token(registry):
    w, state = _held_watcher(registry)
    router, _ = _router(watcher=w)
    # 1) /resume 자체는 집행하지 않는다 — preview + token 요구
    reply = router.handle(OWNER, "/resume")
    assert state.hold and "preview" in reply
    token = reply.rsplit("/confirm ", 1)[1].strip()
    # 2) 틀린 token으로는 불가
    assert "무효 token" in router.handle(OWNER, "/confirm deadbeef")
    assert state.hold
    # 3) 올바른 token → 해제
    assert "재개 완료" in router.handle(OWNER, f"/confirm {token}")
    assert not state.hold


def test_token_is_single_use_and_ttl_bound(registry):
    now = [0.0]
    w, state = _held_watcher(registry)
    router, _ = _router(clock=lambda: now[0], watcher=w)
    token = router.handle(OWNER, "/resume").rsplit("/confirm ", 1)[1].strip()
    router.handle(OWNER, f"/confirm {token}")
    assert "무효 token" in router.handle(OWNER, f"/confirm {token}")  # 단일 사용
    w.hold("again")
    token2 = router.handle(OWNER, "/resume").rsplit("/confirm ", 1)[1].strip()
    now[0] = 301.0
    assert "만료" in router.handle(OWNER, f"/confirm {token2}")       # TTL 5분
    assert state.hold


# ── DoD: 보고서 — 시그널 근거·비용·미검증 표기 ──────────────────────────


def test_morning_report_contains_mandatory_elements():
    text = morning_report(
        date="2026-07-06", strategy_id="momentum-core", lifecycle_state="paper",
        exposure=0.5,
        signal_notes=["AAPL 모멘텀 순위 1 (26주 +18.2%)", "레짐 caution → 노출 0.5"],
        fills=[{"side": "BUY", "symbol": "AAPL", "qty": 1.5,
                "exec_price": 213.55, "commission": 0.32}],
        costs_krw=450.0, pnl_day_krw=12_000.0, equity_krw=5_012_000.0,
        holds=[], unverified=True,
    )
    assert "시그널 근거" in text and "모멘텀 순위" in text
    assert "비용 합계 450" in text
    assert "미검증" in text                       # RISK-04 표기 의무
    assert "BUY AAPL" in text
    verified = morning_report(
        date="d", strategy_id="s", lifecycle_state="live", exposure=1.0,
        signal_notes=[], fills=[], costs_krw=0, pnl_day_krw=0, equity_krw=1,
        unverified=False,
    )
    assert "미검증" not in verified


def test_report_shows_hold_banner():
    text = morning_report(
        date="d", strategy_id="s", lifecycle_state="paper", exposure=0.0,
        signal_notes=[], fills=[], costs_krw=0, pnl_day_krw=0, equity_krw=1,
        holds=["schema_drift"], unverified=True,
    )
    assert "fail-safe hold" in text and "/resume" in text


# ── 텔레그램 클라이언트 — 진짜 urllib로 로컬 서버 왕복 ─────────────────


class _TgServer:
    def __init__(self):
        self.sent: list[dict] = []
        self.updates = [{"update_id": 7, "message": {
            "chat": {"id": OWNER}, "text": "/status"}}]
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                form = self.rfile.read(length).decode()
                if self.path.endswith("/getUpdates"):
                    result = server.updates
                    server.updates = []
                elif self.path.endswith("/sendMessage"):
                    server.sent.append(dict(p.split("=", 1) for p in form.split("&")))
                    result = {}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps({"ok": True, "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_telegram_poll_roundtrip_real_urllib():
    server = _TgServer()
    try:
        client = TelegramClient(
            "tok-test", api_base=server.base, timeout_s=5.0, poll_timeout_s=0.0,
        )
        router, _ = _router()
        offset = poll_once(client, router.handle, None)
        assert offset == 8                         # update_id + 1
        assert server.sent and "text" in server.sent[0]
    finally:
        server.stop()