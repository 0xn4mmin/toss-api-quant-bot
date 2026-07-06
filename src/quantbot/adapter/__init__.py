"""계층 1 · 어댑터 — 유일한 시장 접점 (ARCH-02 v1.1). 상위 패키지 import 금지.

이중 소스 (QUANTBOT-ARCH v1.1):
- official/ — 토스증권 공식 Open API (REST·OAuth2). 시세·종목/시장 정보·계좌·주문 전부.
  실자금이 움직이는 경로는 100% 여기다.
- tossctl/  — 비공식 CLI (subprocess). 공식에 없는 조회만 — flows·지수·스크리너.
  읽기 전용: 주문 토큰은 이 하위에 존재조차 불가능하다 (AST 강제, IMPL-02 v1.1).

두 소스 공통: fail-closed 계약(contracts.Contract) — 스키마 이탈은 부분 수용 없이
SchemaDrift로 전체 거부·상향 보고.
"""
