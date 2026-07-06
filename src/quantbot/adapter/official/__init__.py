"""공식 Open API 소스 — REST·OAuth2 (ARCH-02 v1.1, openapi.tossinvest.com).

시세·종목/시장 정보·계좌·주문 이력·거래 가능 정보를 감싼다. 실자금 경로는 100%
이 소스다. 계약은 공식 OAS 3.1 명세(v1.1.5)를 그대로 미러링한다 — 수치는 전부
문자열 타입(명세 그대로), envelope은 {"result": ...}.

주문 표면(order.py)은 Phase 4에서 GATE 전용 타입과 함께 도입된다 — 그 전까지
이 소스의 HTTP 클라이언트에는 범용 POST 경로 자체가 존재하지 않는다.
"""
