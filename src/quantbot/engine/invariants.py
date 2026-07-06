"""config/invariants.yaml 의 프로젝트 유일 로더 (IMPL-04, ISO-01).

이 모듈만 invariants.yaml 경로를 알 수 있다 — tests/test_architecture.py가
"invariants.yaml" 문자열 리터럴의 존재를 전 모듈에서 검사해 강제한다 (IMPL-02 장치 2).
로드 결과는 frozen dataclass — 로드 후 어떤 코드도 값을 바꿀 수 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quantbot import _yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PATH = _PROJECT_ROOT / "config" / "invariants.yaml"

_SUPPORTED_VERSION = 1


class InvariantsError(ValueError):
    """invariants 파일이 없거나, 형식·범위 검증에 실패했다."""


@dataclass(frozen=True)
class PositionLimits:
    max_weight_pct: float  # INV-01
    max_leverage: float    # INV-02


@dataclass(frozen=True)
class UniversePolicy:
    whitelist_only: bool          # INV-03
    kr_path: str
    us_path: str
    exclude_leveraged_etf: bool   # INV-11 (ARCH v1.1)


@dataclass(frozen=True)
class BacktestLimits:
    max_mdd_pct: float     # INV-04


@dataclass(frozen=True)
class RebalancePolicy:
    min_interval_days: int  # INV-05


@dataclass(frozen=True)
class TurnoverPolicy:
    auto_approve_max_pct: float  # INV-06


@dataclass(frozen=True)
class CircuitBreaker:
    daily_loss_pct: float  # INV-07


@dataclass(frozen=True)
class LifecyclePolicy:
    auto_approve_requires_paper: bool  # INV-08


@dataclass(frozen=True)
class OrderCaps:
    daily_max_count: int           # INV-09
    per_order_max_amount_krw: int  # INV-10


@dataclass(frozen=True)
class Invariants:
    version: int
    position: PositionLimits
    universe: UniversePolicy
    backtest: BacktestLimits
    rebalance: RebalancePolicy
    turnover: TurnoverPolicy
    circuit_breaker: CircuitBreaker
    lifecycle: LifecyclePolicy
    orders: OrderCaps


def _section(data: dict, name: str) -> dict:
    sec = data.get(name)
    if not isinstance(sec, dict):
        raise InvariantsError(f"섹션 누락 또는 형식 오류: {name!r}")
    return sec


def _num(sec: dict, section: str, key: str, *, lo: float, hi: float) -> float:
    val = sec.get(key)
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise InvariantsError(f"{section}.{key}: 숫자가 아니다: {val!r}")
    if not (lo <= val <= hi):
        raise InvariantsError(f"{section}.{key}: 범위 [{lo}, {hi}] 밖: {val!r}")
    return float(val)


def _intval(sec: dict, section: str, key: str, *, lo: int) -> int:
    val = sec.get(key)
    if isinstance(val, bool) or not isinstance(val, int):
        raise InvariantsError(f"{section}.{key}: 정수가 아니다: {val!r}")
    if val < lo:
        raise InvariantsError(f"{section}.{key}: {lo} 이상이어야 한다: {val!r}")
    return val


def _boolval(sec: dict, section: str, key: str) -> bool:
    val = sec.get(key)
    if not isinstance(val, bool):
        raise InvariantsError(f"{section}.{key}: bool이 아니다: {val!r}")
    return val


def _strval(sec: dict, section: str, key: str) -> str:
    val = sec.get(key)
    if not isinstance(val, str) or not val:
        raise InvariantsError(f"{section}.{key}: 비어 있지 않은 문자열이어야 한다: {val!r}")
    return val


# ── INV-11 판정 술어 (ARCH v1.1 GATE-03) ─────────────────────────────────
# 게이트(Phase 3 LC-G1 / Phase 4 GATE-03)가 유니버스 전 종목에 적용한다.
# 입력은 공식 API 종목 마스터의 securityType·leverageFactor (문자열, 명세 그대로).

ETP_SECURITY_TYPES = ("ETF", "FOREIGN_ETF", "ETN")

VERDICT_ELIGIBLE = "eligible"
VERDICT_REJECTED = "rejected"
VERDICT_INDETERMINATE = "indeterminate"  # 자동 승인 제외 — null≠안전 (fail-closed)


def leverage_verdict(security_type: str, leverage_factor: str | None) -> str:
    """INV-11: 레버리지·인버스 ETF/ETN 배제.

    - ETF/ETN 계열: leverageFactor == 1.0 만 eligible. null은 데이터 누락이므로
      '판정 불가'(자동 승인 제외) — null을 안전으로 간주하면 누락 종목이 게이트를
      통과한다. 1.0 외 값(2.0, 3.0, -1.0 인버스 포함)은 rejected.
    - 그 외 유형(일반 주식 등): 명세상 null이 정상 → eligible. non-null인데
      1.0이 아니면 방어적으로 rejected.
    """
    is_etp = security_type in ETP_SECURITY_TYPES
    if leverage_factor is None:
        return VERDICT_INDETERMINATE if is_etp else VERDICT_ELIGIBLE
    try:
        factor = float(leverage_factor)
    except ValueError:
        return VERDICT_INDETERMINATE  # 해석 불가 = 판정 불가 (fail-closed)
    if factor == 1.0:
        return VERDICT_ELIGIBLE
    return VERDICT_REJECTED


def load_invariants(path: str | Path | None = None) -> Invariants:
    """invariants 파일을 읽어 frozen dataclass로 반환한다.

    검증 실패는 InvariantsError — 부분 로드·기본값 대체는 없다.
    """
    p = Path(path) if path is not None else _DEFAULT_PATH
    if not p.is_file():
        raise InvariantsError(f"invariants 파일이 없다: {p}")
    try:
        data = _yaml.load_file(str(p))
    except _yaml.YamlSubsetError as e:
        raise InvariantsError(f"invariants 파싱 실패: {e}") from e

    version = data.get("version")
    if version != _SUPPORTED_VERSION:
        raise InvariantsError(f"지원하지 않는 version: {version!r}")

    pos = _section(data, "position")
    uni = _section(data, "universe")
    bt = _section(data, "backtest")
    reb = _section(data, "rebalance")
    to = _section(data, "turnover")
    cb = _section(data, "circuit_breaker")
    lc = _section(data, "lifecycle")
    od = _section(data, "orders")

    return Invariants(
        version=_SUPPORTED_VERSION,
        position=PositionLimits(
            max_weight_pct=_num(pos, "position", "max_weight_pct", lo=0.0, hi=100.0),
            max_leverage=_num(pos, "position", "max_leverage", lo=0.0, hi=0.0),  # INV-02: 0만 유효
        ),
        universe=UniversePolicy(
            whitelist_only=_boolval(uni, "universe", "whitelist_only"),
            kr_path=_strval(uni, "universe", "kr_path"),
            us_path=_strval(uni, "universe", "us_path"),
            exclude_leveraged_etf=_boolval(uni, "universe", "exclude_leveraged_etf"),
        ),
        backtest=BacktestLimits(
            max_mdd_pct=_num(bt, "backtest", "max_mdd_pct", lo=0.0, hi=100.0),
        ),
        rebalance=RebalancePolicy(
            min_interval_days=_intval(reb, "rebalance", "min_interval_days", lo=1),
        ),
        turnover=TurnoverPolicy(
            auto_approve_max_pct=_num(to, "turnover", "auto_approve_max_pct", lo=0.0, hi=100.0),
        ),
        circuit_breaker=CircuitBreaker(
            daily_loss_pct=_num(cb, "circuit_breaker", "daily_loss_pct", lo=0.0, hi=100.0),
        ),
        lifecycle=LifecyclePolicy(
            auto_approve_requires_paper=_boolval(lc, "lifecycle", "auto_approve_requires_paper"),
        ),
        orders=OrderCaps(
            daily_max_count=_intval(od, "orders", "daily_max_count", lo=1),
            per_order_max_amount_krw=_intval(od, "orders", "per_order_max_amount_krw", lo=1),
        ),
    )
