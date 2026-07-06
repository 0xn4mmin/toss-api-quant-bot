"""GATE 전용 주문 표면 — PreviewReceipt 타입 강제 (IMPL-02 장치 1, §I3 v1.1).

Phase 4에서 구현: preview 합성(현재가·수수료·매수가능금액 조회) → 엔진 발급
confirm token → POST /api/v1/orders (clientOrderId 멱등성 키, 재시도 없음).
그 전까지 이 모듈은 비어 있고, 어떤 모듈도 이것을 import할 수 없다
(아키텍처 테스트 — Phase 4에서 engine.gate 단독 허용으로 완화).
실물 분기는 Phase 7까지 저장소에 존재하지 않는다 (IMPL-07).
"""
