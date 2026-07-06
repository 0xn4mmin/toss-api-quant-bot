"""canonical JSON + sha256 — 순수 리프 유틸 (계층 없음, stdlib 전용).

정렬 키·고정 구분자 — 동일 내용은 항상 동일 바이트. 사전등록 봉인(BT-02),
주문 의도 해시, 판정 아티팩트가 모두 이 한 구현을 쓴다.
"""

from __future__ import annotations

import hashlib
import json


def canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
