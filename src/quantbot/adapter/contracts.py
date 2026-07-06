"""어댑터 공통 계약 기반 (IMPL-03) — 두 소스(official/tossctl)가 공유하는 문법.

모든 계약은 strict + extra="forbid" + frozen: 알 수 없는 필드·누락 필드·타입
불일치는 부분 파싱 없이 전체 거부된다 (Phase 0 invariants 로더와 같은 fail-closed).
검증 실패는 예외이자 신호다 — SchemaDrift payload가 엔진(Phase 5 watcher)에 상향
보고되어 fail-safe hold 트리거로 소비된다. 외부 API의 스키마 변경이 봇 전체에서
흡수되는 지점이 정확히 이 한 곳이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError


@dataclass(frozen=True)
class SchemaDrift:
    """상향 보고용 신호 payload — 엔진이 registry 이벤트·hold 트리거로 소비한다."""

    source: str            # "official" | "tossctl"
    command: tuple[str, ...]
    model: str
    detail: str


class SchemaDriftError(Exception):
    """응답이 계약을 벗어났다 — 부분 수용 없음, 전체 거부."""

    def __init__(self, drift: SchemaDrift) -> None:
        super().__init__(
            f"SchemaDrift[{drift.source}]: {' '.join(drift.command)} → "
            f"{drift.model}: {drift.detail}"
        )
        self.drift = drift


class Contract(BaseModel):
    """전 계약의 공통 설정 — strict(타입 강제) + extra 거부(미지 필드 거부) + 불변."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def validate(source: str, command: tuple[str, ...], data: object, model: type[Contract]):
    """JSON → 계약 검증. 실패는 SchemaDriftError — 유일한 typed 객체 생성 경로."""
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise SchemaDriftError(
            SchemaDrift(source=source, command=command, model=model.__name__, detail=str(e))
        ) from e
