# TODO — 단계별 상세 가이드 (클릭·명령어 단위)

코드는 Phase 0–6 + 보안 감사 수정 + SPY/VIX 데이터 파이프까지 완료(**191 passed**).
아래는 사람만 할 수 있는 일이다. **위에서 아래로, 항목 안에서는 번호 순서대로**
하면 된다. 명령은 전부 저장소 루트(`toss-api-quant-bot/`)의 터미널에서 실행한다.

> 표기: `$ ...` = 터미널에 입력. ✅ = 이렇게 나오면 성공. ⚠ = 멈추고 확인할 것.

---

## 0. 공통 준비 (5분, 새 컴퓨터마다 1회)

1. 터미널 열기 (macOS: `⌘+Space` → "터미널" / Linux: 아무 셸)
2. uv 설치 확인:
   ```
   $ uv --version
   ```
   ✅ `uv 0.x.x` — 없으면: `$ curl -LsSf https://astral.sh/uv/install.sh | sh` 후 터미널 재시작
3. 저장소 준비:
   ```
   $ git clone https://github.com/0xn4mmin/toss-api-quant-bot.git
   $ cd toss-api-quant-bot
   $ uv sync
   $ uv run pytest
   ```
   ✅ 마지막 줄 `191 passed` — ⚠ 하나라도 failed면 출력 전체를 복사해 세션에 붙여넣기 (진행 중단)

---

## 1. 공식 API 키 발급·배치 (15분)

1. 브라우저에서 토스증권 WTS 접속 → 로그인
2. **설정 > Open API** 메뉴 진입 (공식 가이드 "시작하기" 절 기준)
3. 클라이언트 등록 버튼 클릭 → `client_id` 와 `client_secret` 발급됨.
   **secret은 이 화면에서만 보일 수 있다 — 지금 복사**
4. 터미널에서 (붙여넣을 때 따옴표 없이 값만):
   ```
   $ mkdir -p var/secrets
   $ printf '%s' '여기에_client_id_붙여넣기' > var/secrets/toss_client_id
   $ printf '%s' '여기에_client_secret_붙여넣기' > var/secrets/toss_client_secret
   $ chmod 600 var/secrets/toss_client_id var/secrets/toss_client_secret
   ```
5. 확인:
   ```
   $ ls -l var/secrets/
   ```
   ✅ 두 파일 모두 `-rw-------` (600). 다르면 봇이 경고를 찍는다. git에는 절대 안 올라간다(`var/` ignore).

## 2. 계약 실측 검증 — api-verify (10분)

1. 시세·시장 정보 검증 (계좌 무관):
   ```
   $ uv run quantbot api-verify
   ```
   ✅ `OK` 10줄 + `10개 검사 — OK 10, DRIFT 0, ERROR 0`
   ⚠ **DRIFT가 한 줄이라도 나오면**: 출력 전체 복사 → 세션에 붙여넣기.
   계약을 실측으로 확정하는 정상 절차다(§I8) — 버그 아님. ERROR는 네트워크/키 문제.
2. 계좌 번호(accountSeq) 확인 — 위 출력과 별개로:
   ```
   $ uv run python -c "
   from quantbot.cli import _load_runtime, _official_client
   rt = _load_runtime('config/runtime.yaml')
   from quantbot.adapter.official import acct
   for a in acct.accounts(_official_client(rt, '')):
       print(a.accountSeq, a.accountNo, a.accountType)"
   ```
   ✅ `1 123-45-678900 BROKERAGE` 같은 줄 — 첫 숫자가 accountSeq
3. `config/runtime.yaml` 열어서 (`$ open config/runtime.yaml` 또는 편집기)
   `adapter: official:` 아래에 한 줄 추가 (들여쓰기 4칸, base_url과 같은 레벨):
   ```yaml
     account_seq: "1"          # 2에서 확인한 값, 따옴표 포함
   ```
4. 계좌 스코프까지 전수 검증:
   ```
   $ uv run quantbot api-verify --account
   ```
   ✅ `16개 검사 — OK 16` — ⚠ DRIFT/ERROR는 2-1과 동일 처리

## 3. 백테스트 데이터 구축 — SPY + VIX 포함 (20분)

레짐 필터(§S4)의 입력: US 추세=**SPY**(공식 API), 변동성=**VIX**(CBOE 무료 CSV).

1. 유니버스 + SPY 일봉 수신 (심볼은 4번 항목의 화이트리스트대로 수정).
   ⚠ **--days 2400 필수**: OOS는 데이터 시작 + 훈련 3년 뒤부터 시작하므로,
   2020 스트레스 창이 OOS에 들어가려면 데이터가 2017-02 이전에서 시작해야
   한다 (1800일이면 BT-G2 미커버로 무조건 불합격):
   ```
   $ uv run quantbot fetch-candles \
       --symbols SPY,AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,AVGO,COST,NFLX \
       --days 2400 --out var/data/us_raw.csv
   ```
   ✅ 심볼별 `1800봉` 언저리 출력 + `→ var/data/us_raw.csv (...행)`
   ⚠ 봉 수가 심볼마다 크게 다르면(신규 상장 등) 그대로 진행 — 다음 단계가 교집합으로 정렬
2. CBOE VIX 이력 다운로드:
   ```
   $ curl -L -o var/data/VIX_History.csv \
       "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
   $ head -3 var/data/VIX_History.csv
   ```
   ✅ 첫 줄이 `DATE,OPEN,HIGH,LOW,CLOSE` — ⚠ 아니면 URL이 바뀐 것: cboe.com → VIX
   Historical Data에서 CSV 링크를 찾아 대체
3. 병합 + 날짜 격자 정렬:
   ```
   $ uv run quantbot import-vix --data var/data/us_raw.csv \
       --cboe-csv var/data/VIX_History.csv --out var/data/us.csv
   ```
   ✅ `→ var/data/us.csv: N종목 × M일 (시작일~종료일)` — M이 1200일(≈5년) 이상인지,
   **시작일이 2017-02-01 이전**인지 확인 (아니면 멈추고 세션에 보고 — API 깊이 한계)

## 4. 유니버스 화이트리스트 (30분, 검토 작업)

1. `universe/us.yaml` 생성 — 형식 (블록 리스트):
   ```yaml
   # US 화이트리스트 — 사람 큐레이션 (INV-03 · INV-11 1차 필터)
   symbols:
     - AAPL
     - MSFT
     # ... 10~20개
   ```
2. 선정 기준: 대형·고유동성 / **레버리지·인버스 ETF·ETN 금지**(INV-11 — 게이트가
   어차피 거부하지만 애초에 안 넣기) / 최근 상장(3년 미만) 지양(백테스트 이력)
3. ⚠ 현재 백테스트의 실효 유니버스는 **CSV에 들어있는 심볼 전부**다(SPY·VIX는
   레짐 입력으로 자동 제외). 화이트리스트 파일과 fetch-candles의 --symbols를
   일치시켜라. 파일→게이트 자동 배선은 페이퍼 시작 전에 세션에 요청.

## 5. 첫 실데이터 백테스트 — 게이트 판정 (10분 + 해석)

⚠ **실행 전 마지막 확인**: `config/grids/momentum-core.yaml`이 §S10 사전등록
그리드다. **한 번 봉인되면 수정 불가**(수정하면 러너가 거부하고, 재탐색은 새 전략
id 필요). 지금이 그리드를 바꿀 마지막 기회다.

1. 실행:
   ```
   $ uv run quantbot backtest --data var/data/us.csv \
       --index-symbol SPY --vix-symbol VIX
   ```
2. 결과 읽는 법:
   - `사전등록 봉인 xxxx…` — registry에 영구 기록됨
   - `BT-G1~G6: PASS/FAIL` — **전부 PASS여야 paper 전이** (하나라도 FAIL → rejected)
   - `oos_mdd` ≤ 0.15인지, `cost_drag_annual`(연 비용 드래그)이 몇 %인지 주목
3. 시나리오별 다음 행동:
   - ✅ `판정: paper` → 6번(텔레그램)으로
   - `판정: rejected` + BT-G1 FAIL → 전략이 MDD 예산을 못 지킨 것 — **그리드를 고쳐
     재시도하지 말 것**(BT-02 위반). 결과 전체를 세션에 붙여넣기 → 새 가설/새 id 설계
   - `stress_windows_uncovered`가 비어있지 않음 → 데이터 기간 부족 — 3-1의 --days를
     늘려 다시 (단, 봉인이 데이터 범위를 포함하므로 새 전략 id 필요 — 세션과 상의)
   - ⚠ 같은 명령 재실행은 "재현 검산"으로 허용된다(결과 동일 확인용)

## 6. 텔레그램 봇 (20분)

1. 텔레그램 앱에서 `@BotFather` 검색 → 대화 시작
2. `/newbot` 입력 → 봇 이름 입력(아무거나) → username 입력(**bot으로 끝나야 함**,
   예: `nammin_quant_bot`)
3. BotFather가 주는 토큰(`123456:ABC-...` 형태) 복사:
   ```
   $ printf '%s' '토큰_붙여넣기' > var/secrets/telegram_token
   $ chmod 600 var/secrets/telegram_token
   ```
4. **내 chat id 확인**: 방금 만든 봇을 검색해 `/start` 전송 후:
   ```
   $ curl -s "https://api.telegram.org/bot$(cat var/secrets/telegram_token)/getUpdates" | python3 -m json.tool | grep -A2 '"chat"'
   ```
   ✅ `"id": 123456789` — 이 숫자가 chat id
5. `config/runtime.yaml`의 `telegram:` 섹션에서 `owner_chat_id: null` →
   `owner_chat_id: 123456789` (따옴표 없이 숫자)
6. 스모크 테스트:
   ```
   $ uv run quantbot run
   ```
   폰에서 봇에게: `/status` → 응답 오는지 / `/pause` → "일시 중단" /
   `/resume` → **preview + `/confirm xxxx` 토큰 요구**가 오는지(바로 재개되면 버그) /
   `/confirm 토큰` → "재개 완료". 종료는 터미널에서 `Ctrl+C`
   ⚠ `run`은 유일하게 자동 테스트가 없는 조립 코드 — 이상하면 출력째 세션으로

## 7. tossctl + flows 적재 cron (tossctl 확보 시)

1. tossctl 설치·인증 후: `$ tossctl doctor` ✅ 정상
2. 수동 1회 (백필 — 이 순간부터 KR 표본이 쌓인다, 하루 = 표본 하루):
   ```
   $ uv run quantbot collect-flows --screener kr_flows
   ```
   ✅ `적재 완료: 신규 N행 / M종목, 백필 기록됨`
   ⚠ 실측 응답이 계약과 다르면 SchemaDrift로 실패한다 — 출력을 세션에 (계약 확정)
3. cron 등록:
   ```
   $ mkdir -p var/logs
   $ crontab -e
   ```
   편집기가 열리면 `ops/crontab.example`의 마지막 두 줄을 복사해 붙이고,
   `/path/to/toss-api-quant-bot`을 실제 경로(`$ pwd`로 확인)로 바꾼 뒤 저장
4. 다음 거래일에 확인: `$ tail var/logs/collect-flows.log` ✅ `신규 N행`

## 8. 페이퍼 1개월 (LC-G3) — 승격의 전제

1. **시작 전 결정 1개 (소유자만 가능)**: LC-G3 "백테스트 대비 괴리 허용범위" 수치
   (ARCH §11이 비워둔 슬롯). 권장 논의 시작점: 주간 수익률 괴리 ±2%p, 4주 누적
   ±4%p — 결정해서 세션에 알려주면 config·문서에 박는다
2. 시작 전 세션에 요청할 배선 3개: ① 화이트리스트 파일→게이트 ② 리밸런싱
   사이클→run 루프(현재 run은 텔레그램+감시만) ③ 아침 보고서 발신
3. 운영: `uv run quantbot run`을 상시 실행(서버면 `tmux`/`systemd`), 매주 화요일
   아침 보고서에서 체결·괴리 확인
4. 1개월 완주 + 괴리 범위 내 + 치명 오류 0 → 세션에 알리면 `paper_gate_passed`
   기록 절차 진행 (이 기록 없이는 자동 승인이 영구 차단 — INV-08)

## 9. 10만원 실계좌 커미셔닝 (Phase 7 — 페이퍼 통과 후에만)

합의된 순서: 백테스트 → 페이퍼 1개월 → **10만원 커미셔닝** → 본 자본.
10만원 테스트는 배관 검증(주문·체결·대사)이지 성과 검증이 아니다.

1. 페이퍼 통과 기록 확인 후 세션에 "Phase 7 시작" 요청 — 실물 분기
   (POST /orders + clientOrderId 멱등키 + 무재시도)는 이때 처음 구현된다
2. 계좌에 10만원 입금
3. `config/runtime.yaml`: `live_trading: false → true` (이 커밋 자체가 감사 기록)
4. 커미셔닝 절차(§I7 Phase 7 DoD): 최소 단위 1건 매수 → 체결 확인 → registry
   대사 일치 → 1건 매도 왕복 → `quantbot api-verify --account`로 잔고 재확인
5. 그동안 실물 감각이 궁금하면: 토스 **앱에서 수동으로** 1주 매매 (봇 경로 무관)

## 10. 성과 진실성 3종 (백테스트 신뢰도를 바꾸는 작업 — 세션에 요청)

- [ ] **BT-D1 생존편향 유니버스**: 현재 백테스트는 "오늘의 화이트리스트"를 과거에
  적용 — 수익률이 부풀 수 있다. 지수 구성 이력 기반 point-in-time 유니버스 구축
- [ ] **비용 실측 반영**: api-verify의 commissions 실측값 → `config/backtest.yaml`
  costs 갱신 + 페이퍼 실체결과 슬리피지 대사
- [ ] **H1 레짐 ablation 리포트**: 레짐 on/off 두 곡선 비교(§S8) — 필터가 실제로
  MDD를 낮추는지. 안 낮추면 통과했어도 필터 재설계

## 11. 발전 로드맵 — 1·2번 구현 완료 (STRAT v1.4), 판정은 소유자 실행

1. ~~변동성 타게팅 오버레이~~ ✅ 구현됨 — 전략 파일 `sizing.vol_target_annual`/
   `vol_lookback_days` 선언으로 켠다 (dual-momentum.v1.yaml에 예시)
2. ~~자산군 듀얼 모멘텀~~ ✅ 슬롯·전략 파일(draft)·그리드 구현됨. 판정 절차:
   ```
   $ uv run quantbot fetch-candles --symbols SPY,EFA,TLT,GLD,IEF \
       --days 1800 --out var/data/assets_raw.csv
   $ uv run quantbot import-vix --data var/data/assets_raw.csv \
       --cboe-csv var/data/VIX_History.csv --out var/data/assets.csv
   $ uv run quantbot backtest --strategy strategies/dual-momentum.v1.yaml \
       --grid config/grids/dual-momentum.yaml --data var/data/assets.csv \
       --allow-no-regime
   ```
   (dual momentum은 자체 절대 필터가 레짐 역할 — --allow-no-regime이 맞다)
   ⚠ **실행 전 12번의 INV-01 ETF 캡 결정 필수** — 미결정 상태로 돌리면 실효
   노출이 top_n×12%로 잘려 판정이 왜곡된다
3. KR flows sleeve (표본 3년 도달 시 — 이 봇의 고유 엣지)
4. 멀티 전략 + 규칙 기반 메타 배분
- 심어진 지식의 전체 대장: `docs/INVESTMENT_KNOWLEDGE.md` (근거·코드 위치·
  의도적 배제 목록·기대치 눈금)

## 12. 미확정 값·잔여 결정 (시기 도래 시)

- [ ] **INV-01 광범위 ETF 캡 결정** (11-2 전제): 종목당 12% 캡은 개별 주식용 —
  SPY·TLT 같은 분산형 ETF에 예외/상향(예: 지수 ETF 50%)을 둘지. invariants.yaml
  (ARCH §4) 개정 사안이라 소유자만 결정 가능. 결정하면 세션이 검증 로직과 함께 반영
- [ ] GLD·TLT 등 ETF의 leverageFactor 실측 확인 (`api-verify` 후 stocks 조회) —
  null이면 INV-11 판정 불가로 자동 승인에서 빠진다 (1배 ETF는 "1.0"이어야 정상)
- [ ] LC-G3 괴리 허용범위 (8-1 — 페이퍼 시작 전 필수)
- [ ] INV-07(-3%)/09(20회)/10(100만원) — live 승격 전 재검토 약속
- [ ] watcher 임계(하트비트 90s·연속 오류 5회) — 운영 데이터로 보정
- [ ] WS 존재 확인 (문서상 REST만 — 확인되면 Phase 5 폴링 재설계)
- [ ] candles adjusted가 배당까지 포함하는지 실측 확인 (모멘텀 정확도)
- [ ] LLM 번역기: 어떤 LLM·계정 분리(ISO-02)로 갈지 — RL 위원회안은 기각,
  단순 번역기(자연어 목표 → 전략 YAML 초안)로 축소 유지

---
*갱신: 2026-07-06 (STRAT v1.3 반영). 완료 항목은 체크 후 커밋해두면 다음 세션이
상태를 정확히 읽는다.*
