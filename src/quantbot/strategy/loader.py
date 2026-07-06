"""strategies/ 전용 로더 — invariants 경로 접근 금지 (IMPL-02 장치 2). Phase 3에서 구현."""

# DoD 시연용 위반 (§I7 Phase 0): strategy는 어떤 quantbot 계층도 import할 수 없다
from quantbot.adapter import md  # 위반: 계층 방향 (§I1)
_FORBIDDEN = "config/invariants.yaml"  # 위반: 경로 리터럴 (ISO-01)
