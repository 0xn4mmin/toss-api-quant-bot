"""전략 YAML 스키마 (ARCH §5, STRAT §S6) — 선언과 코드의 경계.

파일은 슬롯 이름과 파라미터만 선언한다 — 수식은 slots/에 산다. 불변식과 겹치는
필드는 의도적으로 없다 (충돌 시 불변식이 이기는 게 아니라, 전략 값이 불변식을
만질 인터페이스가 없다). strict + extra=forbid — 오타 필드도 거부(fail-closed).

order_unit (ARCH v1.1 결정 2): whole=정수 수량 집행, fractional=금액 매수 +
소수점 시장가 매도(US 정규장 전용). 백테스트는 선언 단위 그대로 평가하며,
단위 비교는 별개 전략 id의 별개 사전등록으로만 가능하다 (BT-02).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

_SLEEVE_SUM_TOL = 1e-9


class StrategySchemaError(ValueError):
    """스키마 위반 — 부분 수용 없이 전체 거부."""


class _Decl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Meta(_Decl):
    id: str
    version: int
    author: Literal["human", "llm"]
    goal_prompt: str | None = None  # LLM 번역 시 원문 목표 기록 의무 (감사 추적)

    @model_validator(mode="after")
    def _llm_requires_goal(self) -> "Meta":
        if self.author == "llm" and not self.goal_prompt:
            raise ValueError("author=llm 이면 goal_prompt 기록이 의무다 (ARCH §5)")
        return self


class UniverseLeg(_Decl):
    source: Literal["whitelist", "screener"]
    max_symbols: int
    preset: str | None = None       # source=screener 일 때
    min_adv_usd: float | None = None

    @model_validator(mode="after")
    def _screener_needs_preset(self) -> "UniverseLeg":
        if self.source == "screener" and not self.preset:
            raise ValueError("source=screener 이면 preset이 필요하다")
        if self.max_symbols < 1:
            raise ValueError("max_symbols ≥ 1")
        return self


class SignalDecl(_Decl):
    slot: Literal["trend_score", "regime_filter", "kr_flows_score"]
    inputs: list[str]
    params: dict[str, float | int | bool | str]


class EntryDecl(_Decl):
    rule_slot: Literal["rank_top_n"]
    params: dict[str, float | int | bool]


class ExitDecl(_Decl):
    rule_slot: Literal["rank_drop"]
    params: dict[str, float | int | bool]
    stop_loss_pct: float

    @model_validator(mode="after")
    def _stop_range(self) -> "ExitDecl":
        if not (0.0 < self.stop_loss_pct < 1.0):
            raise ValueError(f"stop_loss_pct ∈ (0,1): {self.stop_loss_pct}")
        return self


class EntryExit(_Decl):
    entry: EntryDecl
    exit: ExitDecl


class Sizing(_Decl):
    scheme: Literal["equal_weight_capped"]   # OF-03: 사이징 스킴 자체는 탐색 금지
    sleeves: dict[str, float]
    no_trade_band: float
    order_unit: Literal["whole", "fractional"]  # v1.1 결정 2 — 선언값, 탐색 금지

    @model_validator(mode="after")
    def _sleeves_sum_to_one(self) -> "Sizing":
        if not self.sleeves:
            raise ValueError("sleeves가 비어 있다")
        total = sum(self.sleeves.values())
        if abs(total - 1.0) > _SLEEVE_SUM_TOL:
            raise ValueError(f"sleeve 비중 합이 1이 아니다: {total}")
        if any(v <= 0 for v in self.sleeves.values()):
            raise ValueError("sleeve 비중은 양수여야 한다")
        if not (0.0 <= self.no_trade_band < 1.0):
            raise ValueError(f"no_trade_band ∈ [0,1): {self.no_trade_band}")
        return self


class Cadence(_Decl):
    rebalance: Literal["weekly"]             # INV-05 대비 정적 검사 지점
    execution_window: dict[str, str]


class StrategyFile(_Decl):
    """전략 파일 전체 — 봇 행동을 결정하는 유일한 가변 입력 (ARCH-07)."""

    meta: Meta
    universe: dict[str, UniverseLeg]
    signals: list[SignalDecl]
    entry_exit: EntryExit
    sizing: Sizing
    cadence: Cadence

    @model_validator(mode="after")
    def _consistent(self) -> "StrategyFile":
        if not self.signals:
            raise ValueError("signals가 비어 있다")
        if set(self.sizing.sleeves) != set(self.universe):
            raise ValueError(
                f"sleeves {sorted(self.sizing.sleeves)} ≠ universe {sorted(self.universe)}"
            )
        return self


def parse_strategy(data: dict) -> StrategyFile:
    try:
        return StrategyFile.model_validate(data)
    except ValidationError as e:
        raise StrategySchemaError(str(e)) from e
