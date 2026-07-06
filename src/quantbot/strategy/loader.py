"""strategies/ 전용 로더 (IMPL-04) — 이 디렉토리 밖은 읽지 않는다.

invariants 경로는 이 모듈이 알 수 없다 (아키텍처 테스트가 경로 문자열을 강제).
YAML은 stdlib 서브셋 파서 — flow mapping({...})은 지원하지 않으므로 전략 파일은
블록 스타일로 쓴다 (미지원 구문은 오해석 대신 거부).
"""

from __future__ import annotations

from pathlib import Path

from quantbot import _yaml
from quantbot.strategy.schema import StrategyFile, StrategySchemaError, parse_strategy


class StrategyLoadError(ValueError):
    pass


def load_strategy(path: str | Path, strategies_dir: str | Path = "strategies") -> StrategyFile:
    """strategies/ 아래의 전략 파일 하나를 파싱·검증해 반환한다."""
    p = Path(path).resolve()
    root = Path(strategies_dir).resolve()
    if root not in p.parents and p.parent != root:
        raise StrategyLoadError(
            f"전략 파일은 {root} 아래에만 있을 수 있다: {p} (IMPL-04)"
        )
    if not p.is_file():
        raise StrategyLoadError(f"전략 파일이 없다: {p}")
    try:
        data = _yaml.load_file(str(p))
    except _yaml.YamlSubsetError as e:
        raise StrategyLoadError(f"{p.name}: YAML 파싱 실패: {e}") from e
    try:
        return parse_strategy(data)
    except StrategySchemaError as e:
        raise StrategyLoadError(f"{p.name}: 스키마 위반: {e}") from e
