"""사전등록 봉인 (BT-02, OF-04) — 탐색 전에 그리드를 canonical JSON + sha256으로 봉인.

절차가 코드에 새겨진 지점:
- 봉인은 append-only 레지스트리의 아티팩트다 — Phase 0의 sqlite 트리거가
  UPDATE/DELETE를 ABORT하므로 봉인 후 수정은 물리적으로 불가능하다.
- 러너는 봉인이 존재하고 현재 (그리드, 데이터 범위, 폴드 구성)의 해시가
  일치할 때만 탐색을 실행한다 — 그리드를 1칸이라도 고치면 해시가 어긋난다.
- 같은 전략 id에 다른 내용의 재봉인은 거부된다 — 재탐색은 새 전략 id를 요구한다.
"""

from __future__ import annotations

from quantbot._canon import canonical_json, sha256_hex
from quantbot.engine.registry import Registry

__all__ = ["ARTIFACT_KIND", "PreregError", "canonical_json", "sha256_hex",
           "seal", "require_seal"]

ARTIFACT_KIND = "prereg"


class PreregError(ValueError):
    """봉인 부재·해시 불일치·재봉인 시도."""


def _payload(grid: dict, data_range: tuple[str, str], folds_spec: dict) -> dict:
    if not grid:
        raise PreregError("빈 그리드는 봉인할 수 없다")
    return {
        "grid": grid,
        "data_range": list(data_range),
        "folds_spec": folds_spec,
    }


def seal(
    registry: Registry,
    strategy_id: str,
    grid: dict,
    data_range: tuple[str, str],
    folds_spec: dict,
) -> str:
    """탐색 시작 전에 호출한다. 반환값은 봉인 해시.

    동일 내용 재호출은 멱등(기존 해시 반환), 다른 내용이면 거부 —
    그리드 수정은 새 전략 id의 새 봉인을 요구한다 (BT-02).
    """
    payload = _payload(grid, data_range, folds_spec)
    sha = sha256_hex(canonical_json(payload))
    existing = registry.artifacts(strategy_id=strategy_id, kind=ARTIFACT_KIND)
    if existing:
        if existing[0]["sha256"] == sha:
            return sha  # 멱등 — 같은 내용의 재봉인
        raise PreregError(
            f"전략 {strategy_id!r}에는 이미 다른 내용의 사전등록이 봉인돼 있다 "
            f"(기존 {existing[0]['sha256'][:12]}…, 시도 {sha[:12]}…). "
            "그리드를 바꾸려면 새 전략 id로 처음부터 다시 (BT-02)."
        )
    registry.append_artifact(strategy_id, ARTIFACT_KIND, sha, payload)
    return sha


def require_seal(
    registry: Registry,
    strategy_id: str,
    grid: dict,
    data_range: tuple[str, str],
    folds_spec: dict,
) -> str:
    """러너 진입 관문 — 봉인이 없거나 현재 입력과 해시가 다르면 거부."""
    existing = registry.artifacts(strategy_id=strategy_id, kind=ARTIFACT_KIND)
    if not existing:
        raise PreregError(
            f"전략 {strategy_id!r}의 사전등록 봉인이 없다 — seal() 먼저 (BT-02)"
        )
    sha = sha256_hex(canonical_json(_payload(grid, data_range, folds_spec)))
    if existing[0]["sha256"] != sha:
        raise PreregError(
            f"사전등록 해시 불일치: 봉인 {existing[0]['sha256'][:12]}… ≠ "
            f"현재 입력 {sha[:12]}… — 봉인 후 그리드·범위·폴드가 수정됐다. "
            "재탐색은 새 전략 id로 (BT-02)."
        )
    return sha
