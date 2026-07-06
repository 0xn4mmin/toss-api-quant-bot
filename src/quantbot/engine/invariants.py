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
    whitelist_only: bool   # INV-03
    kr_path: str
    us_path: str


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
