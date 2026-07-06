# TODO — 소유자(사람)만 할 수 있는 일

코드는 Phase 0–6 전부 구현·테스트 완료(184 passed). 아래는 **외부 자원(키·바이너리·
계좌·시간)이 필요해서 기계가 대신할 수 없는 것들**이다. 순서대로 하면 된다.
각 항목의 근거 조항을 병기한다.

---

## 1. 공식 API 키 배치 + 계약 실측 확정 (최우선)

- [ ] WTS 로그인 → 설정 > Open API → `client_id`/`client_secret` 발급
- [ ] 파일 배치 (git에 절대 커밋되지 않음 — `var/`는 ignore):
  ```
  var/secrets/toss_client_id
  var/secrets/toss_client_secret
  chmod 600 var/secrets/*        # ISO 격리
  ```
- [ ] 실측 검증 실행:
  ```
  uv run quantbot api-verify                 # 시세·시장 정보 (계좌 무관)
  uv run quantbot api-verify --account       # 계좌 스코프까지 (읽기 전용)
  ```
  - `--account`는 runtime.yaml `adapter.official.account_seq` 설정 필요
    (계좌 목록은 `api-verify`의 GET /accounts 결과에서 `accountSeq` 확인)
- [ ] **DRIFT가 나오면 출력 전체를 세션에 붙여넣기** → 계약(contracts.py)을
  실측으로 확정한다 (§I8 — 버그가 아니라 정상 작업)
- [ ] 특히 실측 후 고정할 임시 계약 2건: `KrMarketDetail`, 장 세션(`_Session`)
  하위 필드 — 지금은 extra 허용(초안)으로 열려 있다

## 2. tossctl 설치·인증 + flows 적재 가동 (하루 = 표본 하루, §I6)

- [ ] 운영 박스에 tossctl 설치·인증 (`tossctl doctor`로 확인)
- [ ] tossctl 계약 실측: flows/index/screener/doctor 응답이 초안 계약과 다르면
  출력을 세션에 붙여넣기 (`src/quantbot/adapter/tossctl/contracts.py` 확정)
- [ ] 수동 1회 실행으로 백필 확인:
  ```
  uv run quantbot collect-flows --screener kr_flows
  ```
  → registry에 `flows_backfill` 깊이 기록. **3년 미만이면 KR 전략은
  paper-extended** (OF-01) — 이 값이 KR sleeve 일정을 결정한다
- [ ] cron 등록: `ops/crontab.example` 참조 (`crontab -e`) — 매 거래일 16:10 KST
- [ ] 적재 실패 메일이 오는지 확인 (cron MAILTO — 텔레그램은 이후 대체)

## 3. 텔레그램 봇 (Phase 6 운영 전제)

- [ ] BotFather로 봇 생성 → 토큰을 `var/secrets/telegram_token`에 배치
- [ ] 봇에게 아무 메시지 전송 후 chat id 확인 →
  runtime.yaml `telegram.owner_chat_id`에 기입 (현재 null — null이면 run이 거부)
- [ ] `uv run quantbot run`으로 라우터 응답 확인 (/status, /pause, /resume 흐름)
  - **주의: `run`은 유일하게 자동 테스트가 없는 조립 코드다** — 첫 실행은
    감독하에. 이상 동작은 출력째로 세션에 붙여넣기

## 4. 유니버스 화이트리스트 작성 (INV-03 · INV-11 1차 필터 · BT-D1)

- [ ] `universe/us.yaml`, `universe/kr.yaml` 작성 — 사람 큐레이션이 1차 필터,
  기계 검증(leverageFactor)이 게이트에서 주 방어 (2026-07-06 결정)
- [ ] 레버리지·인버스 ETF/ETN 편입 금지 (INV-11 — 게이트가 어차피 거부하지만
  애초에 넣지 않는 것이 1차 방어)
- [ ] BT-D1(생존 편향): 백테스트 유니버스는 "현재 화이트리스트"가 아니라
  시점별 구성이어야 한다 — 지수 구성 이력 기반 구축은 별도 작업
  (공식 API `status`/`delistDate`가 상장폐지 이력을 지원)

## 5. 백테스트 데이터 적재 + 게이트 판정 (키 배치 후)

- [ ] 5년+ 일봉 적재 (2020 폭락·2022 약세장 의무 포함, §S7):
  ```
  uv run quantbot fetch-candles --symbols AAPL,MSFT,... --days 1800 --out var/data/us.csv
  ```
- [ ] BT-D2 실측: 분할 이력이 있는 종목(예: AAPL 2020-08, TSLA·NVDA 등)으로
  adjusted true/false 비교 리포트 확인 — `inconclusive`면 백테스트 무효
- [ ] 게이트 판정 (사전등록은 **1회뿐** — 그리드를 고치면 새 전략 id):
  ```
  uv run quantbot backtest --data var/data/us.csv
  ```
  - config/grids/momentum-core.yaml이 §S10 사전등록 표 — **실행 전 최종 확인**
  - 판정 결과(flags·metrics)를 세션에 붙여넣으면 해석을 돕는다

## 6. 페이퍼 1개월 (LC-G3) — 승격의 전제 (INV-08)

- [ ] 백테스트 통과(backtest→paper 전이) 후 `quantbot run`으로 페이퍼 운영 시작
- [ ] **미확정 스펙 값 결정 필요**: LC-G3의 "괴리 허용범위" 수치 (ARCH §11이
  비워둔 슬롯) — 페이퍼 시작 전에 정해서 문서·config에 박아야 한다
- [ ] 1개월 완주 + 괴리 허용범위 내 + 치명 운영오류 0 → registry에
  `paper_gate_passed` 이벤트 기록 (이 기록이 없으면 자동 승인이 영구 차단됨)

## 7. Phase 7 — 실주문 커미셔닝 (마지막, 별도 세션)

- [ ] 머지 조건(IMPL-07): Phase 0–6 전 테스트 초록 + LC-G3 기록이 registry에 존재
- [ ] 실물 분기 구현 요청 (현재 코드에는 존재하지 않음 —
  `adapter/official/order.py`가 NotImplementedError):
  POST /api/v1/orders + `clientOrderId` 멱등키 + 재시도 0
- [ ] runtime.yaml `live_trading: true` (기본 false)
- [ ] 커미셔닝: 최소 금액 1주(또는 소수점 최소 단위) 실주문 1건 왕복 —
  place → 체결 확인 → registry 대사 일치

## 8. 미확정·재검토 항목 (시기 도래 시)

- [ ] **WS 존재 확인** — 공식 문서는 REST만 명시. WS 접근이 확인되면 Phase 5
  폴링 설계 재검토 (2026-07-06 보류 결정)
- [ ] INV-07(-3%)/09(20회)/10(100만) — **페이퍼/live 승격 전 재검토**
  (invariants.yaml 주석에 명시된 약속)
- [ ] watcher 임계(하트비트 90s·연속 오류 5회) — 운영 데이터로 보정
- [ ] 비용 모델 실측 갱신: `api-verify`의 commissions 실측값 →
  config/backtest.yaml costs 갱신, 페이퍼 실체결과 대사해 슬리피지 갱신 (BT-05)
- [ ] KR sleeve 활성화 (v2+): flows 표본 3년 도달 시 — 새 전략 파일(sleeves에
  kr_satellite 추가) + 새 사전등록 + 램프업 계획 (§S3 OF-01)
- [ ] `whole` 단위 전략을 시험하려면: **별개 전략 id**로 새 파일 + 새 사전등록
  (OOS로 단위를 고르는 것은 BT-02 위반 — 봉인 해시가 어차피 거부한다)

## 9. 구현 잔여 (외부 자원 필요해서 스텁으로 남김)

- [ ] **LLM 번역기** (`strategy/translator/`, `quantbot translate`) — 어떤 LLM을
  쓸지(API 키·프로세스 분리 계정) 결정 필요. ISO-02: 번역기 프로세스는
  strategies/ 쓰기만 가능, config/ 접근 불가 — **파일 권한 설정은 배포 시 수동**
- [ ] `run`의 미배선 핸들러: /switch(자동 승인 파이프라인 연결),
  /order(수동 주문), /promote — 페이퍼 운영 시작 전에 요청하면 배선한다
- [ ] 아침 보고서를 리밸런싱 사이클에 연결해 텔레그램 발신 (reports.py는 완성,
  발신 배선은 /switch 배선과 함께)

---
*생성: 2026-07-06 · Phase 0–6 구현 세션. 각 완료 항목은 체크 후 커밋해두면
다음 세션이 상태를 정확히 읽는다.*
