# toss-api-quant-bot

토스증권 **공식 Open API**(REST·OAuth2) 기반의 개인용 주간 리밸런싱 퀀트 봇.
비공식 CLI(tossctl)는 공식 API에 없는 조회(수급 flows·지수·스크리너)에만 읽기
전용으로 쓴다 — **비공식 경로로는 주문이 코드상 표현조차 불가능하다** (AST 강제).

> ⚠ **투자 고지**: 이 저장소의 어떤 전략도 게이트(백테스트→페이퍼 1개월)를
> 통과하기 전까지 "미검증"이다. 백테스트 통과가 미래 성과를 보장하지 않는다.
> 실주문 경로는 페이퍼 게이트 통과 기록이 있어야만 구현·활성화된다.

## 설계 — 4계층, 명령은 아래로만

```
Interface (텔레그램)      2등급 라우팅 — 비가역 명령은 preview → confirm token(5분·1회)
   ↓                      불변식 변경 명령은 존재하지 않음 (스냅샷 테스트로 고정)
Strategy (선언적 YAML)    전략 = 코드가 아니라 파일. LLM도 사람도 같은 게이트를 통과
   ↓
실행 엔진 — 단일 안전 게이트   불변식 검사 → preview → confirm → execute
   ↓                          이 띠를 우회하는 코드 경로가 존재하지 않음
어댑터 (유일한 시장 접점)
 ├─ official/  공식 Open API — 시세·계좌·주문 100% (실자금 경로)
 └─ tossctl/   비공식 CLI — flows·지수·스크리너, 읽기 전용
```

### 안전 불변이 "관습"이 아니라 "구조"인 지점

| 불변 | 강제 방식 |
|---|---|
| preview 없는 execute 불가 | `execute(receipt)`는 봉인 생성자를 가진 영수증 타입만 수용 — 문자열 token 오버로드가 없음. 위조는 해시 재대조, 만료는 TTL이 거부 |
| 불변식 검사 없는 주문 불가 | 게이트는 `ClearedIntent`만 수용 — `caps.check()`만 이 타입을 생성 가능 |
| 비공식 API 실주문 불가 | `adapter/tossctl/` 하위에 주문 토큰을 품은 문자열 상수 자체가 존재 불가(AST, 독스트링 포함) + 명령 allowlist |
| 레지스트리 위조 불가 | 전 테이블 `BEFORE UPDATE/DELETE RAISE(ABORT)` — 수정 SQL 자체가 실패. 정정은 새 행(이벤트 소싱)만 |
| 계층 침범 불가 | `tests/test_architecture.py`가 AST로 전 모듈의 import 방향·경로 문자열·격리 규칙을 검사 — 위반 = CI 빨강 |
| 스키마 드리프트 = 동결 | 두 소스 공통 fail-closed 계약(strict+extra 거부+불변). 이탈 응답은 부분 수용 없이 `SchemaDrift` → fail-safe hold |
| 과최적화 방어 | 탐색 그리드는 **사전등록 봉인**(sha256, append-only) — 1칸만 고쳐도 러너 거부. **OOS는 전략 id당 1회**. 시도 조합 수는 러너가 registry에 강제 기록 |
| 레버리지·인버스 배제 (INV-11) | 공식 종목 마스터 `leverageFactor` 기계 검증 — ETF/ETN의 null은 "안전"이 아니라 "판정 불가"(자동 승인 제외) |
| fail-safe hold | 하트비트 소실·연속 오류·드리프트·유령 체결 → 전면 동결(자동 손절도 중단). 해제는 사람의 Tier-2 confirm뿐, **재시작해도 registry에서 hold 복원** |

## 전략 (v1: US 코어 단독)

- **횡단면 모멘텀** (12−1 계열, 순위 기반) + **3단 레짐 필터**(지수 MA × VIX —
  노출 1.0/0.5/e_min) + 진입/청산 히스테리시스 + 종목당 12% 캡 선제 클리핑.
- 파라미터 값은 코드에 없다 — 탐색 범위는 `config/grids/`에 사전등록, 최종값은
  워크포워드가 고른 **평탄 지대의 중앙값**(뾰족한 봉우리는 성과가 좋아도 탈락).
- 합격은 6개 게이트 AND: OOS MDD ≤15% / 부트스트랩 p95 MDD ≤20% + **스트레스 창
  (2020·2022) 의무 커버** / 세후 CAGR>0 & 샤프≥0.5 / 평탄 지대 / IS 대비 반감
  이내 / 소액 계좌 비용 현실성.
- KR 수급(sleeve)은 flows 표본 3년 축적 전까지 paper 전용.

## 빠른 시작

```bash
git clone https://github.com/0xn4mmin/toss-api-quant-bot.git && cd toss-api-quant-bot
uv sync
uv run pytest            # 190 passed — 외부 의존 없이 전부 로컬
```

운영 셋업(키·크론·텔레그램)과 남은 사람 작업은 **[TODO.md](TODO.md)** 참조.

```bash
# 1) 공식 API 키 배치 후 — 계약 실측 검증
uv run quantbot api-verify --account

# 2) 백테스트 데이터 적재 (수정주가 일봉)
uv run quantbot fetch-candles --symbols AAPL,MSFT,... --days 1800 --out var/data/us.csv

# 3) 게이트 판정 (사전등록 봉인 — 그리드 수정 시 거부)
uv run quantbot backtest --data var/data/us.csv --index-symbol SPX --vix-symbol VIX

# 4) KR flows 일일 적재 (tossctl 필요, cron: ops/crontab.example)
uv run quantbot collect-flows --screener kr_flows

# 5) 페이퍼 운영 (텔레그램 토큰·owner_chat_id 필요)
uv run quantbot run
```

## 구조

```
config/          invariants.yaml(사람만 편집) · runtime.yaml · backtest*.yaml · grids/
strategies/      선언적 전략 파일 (유일한 LLM 쓰기 허용 경로)
universe/        화이트리스트 (INV-03·11의 1차 필터)
src/quantbot/
  adapter/       official/(OAS 미러 계약) · tossctl/(subprocess) · fills.py(체결 계보)
  engine/        invariants · caps · gate · portfolio · approval · scheduler ·
                 watcher · reconcile · registry(append-only)
  strategy/      schema · loader · slots/(순수 함수) · translator/(스텁)
  backtest/      data(as_of 뷰) · sim · walkforward · judge · prereg
  collect/       flows_snapshot (일일 수급 적재)
  interface/     telegram · router · reports
  cli.py         api-verify | fetch-candles | backtest | collect-flows | run
tests/           190 tests — test_architecture.py가 계층 규칙의 문지기
var/             런타임 상태 (git 밖) — registry.db · flows.db · secrets/
```

## 스펙 추적성

모든 커밋·코드 주석이 상위 문서의 조항 ID를 참조한다:
QUANTBOT-ARCH v1.1(ARCH·INV·GATE·LC·TG·ISO·RISK) ·
QUANTBOT-STRAT v1.2(SIG·BT·OF·H1) · QUANTBOT-IMPL v1.1(IMPL).
스펙과 코드가 어긋나면 **코드로 우회하지 않고 문서를 먼저 개정**한다 —
문서가 소스 오브 트루스다.

## 라이선스 / 책임

개인 프로젝트. 실자금 운용의 모든 책임은 소유자에게 있다.
`var/secrets/`의 자격증명은 절대 커밋되지 않으며(gitignore), 파일 권한 600을
권장한다(어댑터가 경고).
