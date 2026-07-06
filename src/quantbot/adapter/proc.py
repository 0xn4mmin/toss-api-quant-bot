"""tossctl subprocess 실행기 (IMPL-03, ARCH-02) — 호출의 물리학.

프로젝트에서 subprocess를 import할 수 있는 유일한 모듈 (IMPL-02 장치 2,
tests/test_architecture.py가 강제).

구조로 강제되는 것:
- 인자는 배열로만 조립 — 셸 문자열 경로가 없어 인젝션이 표현 불가능하다.
- 주문 계열(첫 토큰 "order")은 재시도 0회가 정책이 아니라 attempts_for()의
  반환값이다 — 중복 주문이 실패보다 나쁘다 (§I3).
- Phase 2 조회 표면에서 주문 네임스페이스는 실행 자체가 차단된다
  (enable_order_namespace 기본 False — Phase 4의 gate 전용 표면만 켠다).
- 수치(타임아웃·재시도·간격)는 config/runtime.yaml adapter 섹션 주입.

push listen용 JSONL 스트리밍은 Phase 5(stream.py)에서 이 모듈에 추가된다.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from quantbot import _yaml

ORDER_FAMILY = ("order",)  # 주문 네임스페이스 — 재시도 금지·Phase 2 차단 대상
JSON_OUTPUT_FLAG = ("--output", "json")


class TossctlError(Exception):
    """어댑터 실행 계층의 공통 예외."""


class TossctlTimeout(TossctlError):
    pass


class TossctlFailed(TossctlError):
    """비정상 종료 (재시도 소진 후)."""

    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        super().__init__(f"tossctl {' '.join(args)} → exit {returncode}: {stderr.strip()[:500]}")
        self.returncode = returncode
        self.stderr = stderr


class TossctlBadJson(TossctlError):
    """stdout이 JSON이 아니다 — 스키마 이전 단계의 실패."""


class OrderNamespaceBlocked(TossctlError):
    """조회 표면에서 주문 네임스페이스 호출 시도 — 설계상 존재하지 않는 경로."""


@dataclass(frozen=True)
class RunPolicy:
    binary: str
    timeout_s: float
    max_retries: int          # 조회 계열의 추가 시도 횟수
    backoff_base_s: float
    rate_min_interval_s: float

    @classmethod
    def from_config(cls, cfg: dict) -> "RunPolicy":
        binary = cfg.get("binary")
        if not isinstance(binary, str) or not binary:
            raise TossctlError(f"adapter.binary: 문자열 필요: {binary!r}")
        vals = {}
        for key in ("timeout_s", "max_retries", "backoff_base_s", "rate_min_interval_s"):
            v = cfg.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise TossctlError(f"adapter.{key}: 0 이상 숫자 필요: {v!r}")
            vals[key] = v
        return cls(
            binary=binary,
            timeout_s=float(vals["timeout_s"]),
            max_retries=int(vals["max_retries"]),
            backoff_base_s=float(vals["backoff_base_s"]),
            rate_min_interval_s=float(vals["rate_min_interval_s"]),
        )

    @classmethod
    def from_runtime_yaml(cls, path: str | Path) -> "RunPolicy":
        data = _yaml.load_file(str(path))
        adapter = data.get("adapter")
        if not isinstance(adapter, dict):
            raise TossctlError(f"{path}: adapter 섹션이 없다")
        return cls.from_config(adapter)


def is_order_family(args: list[str]) -> bool:
    return bool(args) and args[0] in ORDER_FAMILY


def attempts_for(args: list[str], policy: RunPolicy) -> int:
    """주문 계열은 무조건 1회 — 재시도 없음이 코드 구조다 (§I3)."""
    if is_order_family(args):
        return 1
    return 1 + policy.max_retries


class TossctlRunner:
    """tossctl 호출의 유일한 관문. 인자 배열 → JSON 파싱까지만 책임진다
    (스키마 검증은 contracts.call이 얹는다)."""

    def __init__(
        self,
        policy: RunPolicy,
        *,
        enable_order_namespace: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._enable_order = enable_order_namespace
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None

    def _rate_limit(self) -> None:
        if self._last_call is not None:
            elapsed = self._clock() - self._last_call
            wait = self._policy.rate_min_interval_s - elapsed
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def run_json(self, args: list[str]) -> object:
        """tossctl <args> --output json 을 실행해 파싱된 JSON을 반환한다."""
        if not isinstance(args, list) or not args or not all(
            isinstance(a, str) for a in args
        ):
            raise TossctlError(f"인자는 비어 있지 않은 문자열 배열이어야 한다: {args!r}")
        if is_order_family(args) and not self._enable_order:
            raise OrderNamespaceBlocked(
                "주문 네임스페이스는 조회 표면에 존재하지 않는다 — "
                "Phase 4의 GATE 전용 표면(adapter.order)만 사용할 수 있다"
            )
        cmd = [self._policy.binary, *args, *JSON_OUTPUT_FLAG]
        attempts = attempts_for(args, self._policy)
        last_exc: TossctlError | None = None
        for attempt in range(attempts):
            if attempt > 0:
                self._sleep(self._policy.backoff_base_s * (2 ** (attempt - 1)))
            self._rate_limit()
            try:
                proc = subprocess.run(  # 배열 인자 — shell=False가 기본이자 유일 경로
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self._policy.timeout_s,
                )
            except subprocess.TimeoutExpired:
                last_exc = TossctlTimeout(
                    f"tossctl {' '.join(args)} — {self._policy.timeout_s}s 초과"
                )
                continue
            if proc.returncode != 0:
                last_exc = TossctlFailed(args, proc.returncode, proc.stderr)
                continue
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError as e:
                # JSON 자체가 깨진 응답은 재시도 대상이 아니라 즉시 상향 신호
                raise TossctlBadJson(
                    f"tossctl {' '.join(args)}: stdout이 JSON이 아니다: {e}"
                ) from e
        assert last_exc is not None
        raise last_exc
