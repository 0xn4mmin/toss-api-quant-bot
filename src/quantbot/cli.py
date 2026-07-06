"""진입점 (IMPL-01) — 조립 루트로서 유일하게 전 계층을 이어 붙인다.

구현된 명령:
  quantbot api-verify    공식 API 계약 실측 검증 — 조회 표면을 실제로 호출해
                         계약(OAS 미러)과 대조하고 SchemaDrift를 보고한다.
                         기본은 시세·시장 정보(계좌 무관)만, --account로 확장.
  quantbot collect-flows KR flows 일일 적재 (IMPL-06, Phase 2.5).

run | backtest | paper | report 는 후속 Phase에서 채운다.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from quantbot import _yaml

DEFAULT_RUNTIME = "config/runtime.yaml"


def _load_runtime(path: str) -> dict:
    data = _yaml.load_file(path)
    if not isinstance(data.get("adapter"), dict):
        raise SystemExit(f"{path}: adapter 섹션이 없다")
    return data


def _official_client(runtime: dict, runtime_path: str):
    from quantbot.adapter.official.http import Credentials, OpenApiClient, OpenApiPolicy

    cfg = runtime["adapter"]["official"]
    policy = OpenApiPolicy.from_config(cfg)
    creds = Credentials.from_files(cfg["client_id_path"], cfg["client_secret_path"])
    return OpenApiClient(policy, creds, account_seq=cfg.get("account_seq"))


def cmd_api_verify(args: argparse.Namespace) -> int:
    """조회 표면을 실호출해 계약과 대조한다 — 계약 확정(§I8)의 실측 도구."""
    from quantbot.adapter.contracts import SchemaDriftError
    from quantbot.adapter.official import acct, ledger, md, mkt, tradeinfo
    from quantbot.adapter.official.http import OpenApiAuthError, OpenApiError

    runtime = _load_runtime(args.runtime)
    try:
        client = _official_client(runtime, args.runtime)
    except OpenApiAuthError as e:
        cfg = runtime["adapter"]["official"]
        print(f"자격증명 없음: {e}\n"
              f"→ WTS 설정 > Open API에서 발급한 키를 다음 경로에 놓으세요:\n"
              f"   {cfg['client_id_path']}\n   {cfg['client_secret_path']}")
        return 2
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    checks: list[tuple[str, object]] = [
        ("GET /prices", lambda: md.prices(client, symbols)),
        ("GET /orderbook", lambda: md.orderbook(client, symbols[0])),
        ("GET /trades", lambda: md.trades(client, symbols[0])),
        ("GET /price-limits", lambda: md.price_limits(client, symbols[0])),
        ("GET /candles(adjusted)", lambda: md.candles(client, symbols[0], "1d", 5)),
        ("GET /stocks", lambda: md.stocks(client, symbols)),
        ("GET /stocks/{s}/warnings", lambda: md.warnings(client, symbols[0])),
        ("GET /exchange-rate", lambda: mkt.exchange_rate(client)),
        ("GET /market-calendar/KR", lambda: mkt.market_calendar_kr(client)),
        ("GET /market-calendar/US", lambda: mkt.market_calendar_us(client)),
    ]
    if args.account:
        checks += [
            ("GET /accounts", lambda: acct.accounts(client)),
            ("GET /holdings", lambda: acct.holdings(client)),
            ("GET /orders", lambda: ledger.orders_list(client)),
            ("GET /buying-power", lambda: tradeinfo.buying_power(client, "KRW")),
            ("GET /sellable-quantity",
             lambda: tradeinfo.sellable_quantity(client, symbols[0])),
            ("GET /commissions", lambda: tradeinfo.commissions(client)),
        ]

    drifts = errors = 0
    for name, fn in checks:
        try:
            fn()
        except SchemaDriftError as e:
            drifts += 1
            print(f"DRIFT {name}\n      {e}")
        except OpenApiError as e:
            errors += 1
            print(f"ERROR {name}: {e}")
        else:
            print(f"OK    {name}")
    print(f"\n{len(checks)}개 검사 — OK {len(checks) - drifts - errors}, "
          f"DRIFT {drifts}, ERROR {errors}")
    if drifts:
        print("DRIFT = 계약(contracts.py)을 실측에 맞춰 확정해야 한다 (§I8 정상 작업)")
    return 1 if (drifts or errors) else 0


def cmd_collect_flows(args: argparse.Namespace) -> int:
    """KR flows 일일 적재 (IMPL-06). cron이 매 거래일 장 마감 후 호출한다."""
    from quantbot.adapter.tossctl import mkt as tossctl_mkt
    from quantbot.adapter.tossctl.proc import RunPolicy, TossctlRunner
    from quantbot.collect.flows_snapshot import FlowsStore, snapshot
    from quantbot.engine.registry import Registry

    runtime = _load_runtime(args.runtime)
    runner = TossctlRunner(RunPolicy.from_config(runtime["adapter"]["tossctl"]))
    symbols = [s.strip() for s in (args.symbols or "").split(",") if s.strip()]
    if args.screener:
        result = tossctl_mkt.screener(runner, args.screener)
        symbols += [row.symbol for row in result.rows]
    if not symbols:
        raise SystemExit("종목이 없다 — --symbols 또는 --screener 필요")

    var_dir = Path(args.var_dir)
    with Registry(var_dir / "registry.db") as registry, \
         FlowsStore(var_dir / "flows.db") as store:
        result = snapshot(runner, registry, store, symbols)
    new_total = sum(result.new_rows_by_symbol.values())
    print(f"적재 완료: 신규 {new_total}행 / {len(set(symbols))}종목"
          + (f", 백필 기록됨" if result.backfilled else ""))
    if result.failures:
        print(f"실패 {len(result.failures)}건: {sorted(result.failures)}")
        return 1
    return 0


def cmd_fetch_candles(args: argparse.Namespace) -> int:
    """공식 API 일봉(adjusted=true)을 페이지네이션으로 받아 백테스트 CSV를 만든다."""
    import csv as csv_mod

    from quantbot.adapter.official import md

    runtime = _load_runtime(args.runtime)
    client = _official_client(runtime, args.runtime)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    page_size = int(runtime["adapter"]["official"].get("candles_page_size", 200))

    rows: list[tuple[str, str, float, float]] = []
    for symbol in symbols:
        before: str | None = None
        got = 0
        while got < args.days:
            page = md.candles(client, symbol, "1d",
                              min(page_size, args.days - got), before=before)
            if not page.candles:
                break
            for c in page.candles:
                close = float(c.closePrice)
                rows.append((c.timestamp[:10], symbol, close,
                             float(c.volume) * close))
            got += len(page.candles)
            before = page.nextBefore
            if before is None:
                break
        print(f"{symbol}: {got}봉")
    rows.sort()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv_mod.writer(f)
        w.writerow(["date", "symbol", "close", "traded_value"])
        w.writerows(rows)
    print(f"→ {out} ({len(rows)}행) — 백테스트 데이터 (BT-D2: adjusted=true)")
    return 0


def cmd_import_vix(args: argparse.Namespace) -> int:
    """CBOE VIX 이력 CSV를 candles CSV에 병합한다 (STRAT v1.3 — VIX 이력은
    공식 API에 없음). 전 종목 날짜 격자를 교집합으로 정렬해 스토어 정합을 맞춘다."""
    import csv as csv_mod
    from datetime import datetime

    def parse_date(raw: str) -> str:
        raw = raw.strip()
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                continue
        raise SystemExit(f"VIX CSV 날짜 형식을 모른다: {raw!r}")

    # 1) 기존 candles CSV
    rows_by_symbol: dict[str, dict[str, tuple[float, float]]] = {}
    with open(args.data, newline="", encoding="utf-8") as f:
        for r in csv_mod.DictReader(f):
            rows_by_symbol.setdefault(r["symbol"], {})[r["date"]] = (
                float(r["close"]), float(r.get("traded_value") or 0.0),
            )
    # 2) CBOE CSV (DATE,OPEN,HIGH,LOW,CLOSE)
    vix: dict[str, tuple[float, float]] = {}
    with open(args.cboe_csv, newline="", encoding="utf-8") as f:
        reader = csv_mod.DictReader(f)
        cols = {c.upper(): c for c in (reader.fieldnames or [])}
        if "DATE" not in cols or "CLOSE" not in cols:
            raise SystemExit(f"CBOE CSV 헤더에 DATE/CLOSE 필요: {reader.fieldnames}")
        for r in reader:
            vix[parse_date(r[cols["DATE"]])] = (float(r[cols["CLOSE"]]), 0.0)
    rows_by_symbol[args.vix_symbol] = vix

    # 3) 날짜 교집합 정렬 (스토어는 완전 격자를 요구 — 결측은 자르고 보고한다)
    common = set.intersection(*(set(d) for d in rows_by_symbol.values()))
    if not common:
        raise SystemExit("교집합 날짜가 없다 — 데이터 기간을 확인해라")
    dates = sorted(common)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv_mod.writer(f)
        w.writerow(["date", "symbol", "close", "traded_value"])
        for symbol in sorted(rows_by_symbol):
            for d in dates:
                close, tv = rows_by_symbol[symbol][d]
                w.writerow([d, symbol, f"{close:.6f}", f"{tv:.2f}"])
    for symbol in sorted(rows_by_symbol):
        dropped = len(rows_by_symbol[symbol]) - len(dates)
        if dropped:
            print(f"  {symbol}: 교집합 밖 {dropped}일 제외")
    print(f"→ {args.out}: {len(rows_by_symbol)}종목 × {len(dates)}일 "
          f"({dates[0]}~{dates[-1]})")
    return 0


def _grid_signal_builder(
    strategy, tdpw: int,
    index_symbol: str | None = None,
    vix_symbol: str | None = None,
):
    """그리드 조합(주 단위 선언)을 슬롯 파라미터(거래일)로 번역하는 SignalFn.

    index/vix 심볼이 주어지면 그리드의 레짐 파라미터(ma_len·vix_threshold·e_min)와
    전략 파일의 caution_exposure를 함께 배선한다 — 레짐 필터는 MDD 예산의 첫
    방어선(§S4)이라 백테스트에서 빠지면 판정 자체가 왜곡된다.
    """
    from quantbot.strategy.slots.pipeline import build_us_core_signal

    regime_decl = next(
        (d for d in strategy.signals if d.slot == "regime_filter"), None
    )

    def signal_fn_factory(cap: float):
        def signal_fn(view, params):
            slot_params = {
                "lookback": int(params["lookback_wk"]) * tdpw,
                "skip": int(params["skip_wk"]) * tdpw,
                "abs_filter": bool(params.get("abs_filter", False)),
                "n": int(params["n"]),
                "exit_buffer": float(params["exit_buffer"]),
            }
            if index_symbol and vix_symbol:
                slot_params.update({
                    "ma_len": int(params["ma_len"]),
                    "vix_threshold": float(params["vix_threshold"]),
                    "e_min": float(params["e_min"]),
                    "caution_exposure": float(
                        regime_decl.params["caution_exposure"]
                    ),
                })
            fn = build_us_core_signal(
                slot_params, cap=cap,
                index_symbol=index_symbol, vix_symbol=vix_symbol,
            )
            return fn(view, None)
        return signal_fn

    return signal_fn_factory


def cmd_backtest(args: argparse.Namespace) -> int:
    """전략 파일 + 사전등록 그리드 + CSV 데이터로 백테스트 게이트 판정 1회.

    사전등록(BT-02)·OOS 1회(IMPL-05)·게이트 상수(§S9)가 전부 구조로 강제된다.
    """
    from quantbot import _yaml as yaml_mod
    from quantbot.adapter.fills import CostModel
    from quantbot.backtest import judge, prereg, walkforward
    from quantbot.backtest.config import load_gates, load_grid, load_methodology
    from quantbot.backtest.data import MarketDataStore
    from quantbot.engine.invariants import load_invariants
    from quantbot.engine.registry import Registry
    from quantbot.strategy.loader import load_strategy

    strategy = load_strategy(args.strategy)
    grid = load_grid(args.grid)
    meth, costs_cfg = load_methodology(args.config)
    gates = load_gates(args.gates)
    cost_model = CostModel.from_config(costs_cfg)
    inv = load_invariants()
    cap = inv.position.max_weight_pct / 100.0
    tdpw = int(yaml_mod.load_file(args.config)["methodology"].get(
        "trading_days_per_week", 5))
    # 레짐 필터 fail-closed: 전략이 선언했는데 입력이 없으면 데이터를 열기 전에 거부
    declares_regime = any(d.slot == "regime_filter" for d in strategy.signals)
    index_symbol = args.index_symbol or None
    vix_symbol = args.vix_symbol or None
    if declares_regime and not (index_symbol and vix_symbol):
        if not args.allow_no_regime:
            raise SystemExit(
                "전략이 regime_filter를 선언했다 — --index-symbol/--vix-symbol로 "
                "데이터 열을 지정하거나, 명시적으로 --allow-no-regime을 줘라 "
                "(레짐 없는 판정은 MDD 방어선이 빠진 왜곡이다, §S4)"
            )
        print("⚠ --allow-no-regime: 레짐 필터 없이 판정 — 결과는 §S8 H1 검증에 못 쓴다")

    store = MarketDataStore.from_csv(args.data)
    data_range = (args.start or store.date(0),
                  args.end or store.date(len(store) - 1))
    sid = f"{strategy.meta.id}.v{strategy.meta.version}"
    order_unit = strategy.sizing.order_unit
    for s in (index_symbol, vix_symbol):
        if s and s not in store.symbols:
            raise SystemExit(f"데이터에 {s} 열이 없다 — fetch-candles로 포함시켜라")

    signal_fn = _grid_signal_builder(strategy, tdpw, index_symbol, vix_symbol)(cap)

    with Registry(Path(args.var_dir) / "registry.db") as registry:
        sha = prereg.seal(registry, sid, grid, data_range,
                          walkforward.folds_spec(meth, order_unit))
        print(f"사전등록 봉인 {sha[:12]}… (order_unit={order_unit})")
        res = judge.evaluate_oos(
            registry, store, sid, grid, data_range,
            signal_fn, cost_model, meth, gates, order_unit,
        )
    print(f"판정: {'재현 검산' if res.reproduction else res.transition}"
          f" · 시도 {res.n_configs_tried}조합 · artifact {res.artifact_sha[:12]}…")
    for k, v in res.flags.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")
    for k in ("oos_mdd", "oos_sharpe", "oos_cagr_after_costs_taxes",
              "bootstrap_mdd_percentile", "annual_turnover", "cost_drag_annual"):
        print(f"  {k} = {res.metrics[k]:.4f}")
    print(f"  selected = {res.selected_params}")
    return 0 if res.transition == "paper" or res.reproduction else 1


def cmd_run(args: argparse.Namespace) -> int:
    """페이퍼 운영 루프 — 텔레그램 폴링 + 하트비트 + 주간 리밸런싱. (미검증:
    실 키·봇 토큰 배치 후 첫 감독 실행은 TODO.md 참조. live_trading은 Phase 7 전
    까지 무조건 페이퍼로 강제된다.)"""
    from quantbot.adapter.fills import CostModel
    from quantbot.adapter.official import md
    from quantbot.backtest.config import load_methodology
    from quantbot.engine import caps as caps_mod
    from quantbot.engine.gate import Gate, PaperPortfolio
    from quantbot.engine.invariants import load_invariants
    from quantbot.engine.registry import Registry
    from quantbot.engine.watcher import Watcher, WatcherConfig
    from quantbot.interface.router import ROUTING, Router, TokenStore
    from quantbot.interface.telegram import TelegramClient, poll_once

    runtime = _load_runtime(args.runtime)
    tg_cfg = runtime.get("telegram", {})
    owner = tg_cfg.get("owner_chat_id")
    if not isinstance(owner, int):
        raise SystemExit("runtime.yaml telegram.owner_chat_id 필요 (TODO.md 참조)")
    client = _official_client(runtime, args.runtime)
    _, costs_cfg = load_methodology("config/backtest.yaml")
    inv = load_invariants()
    registry = Registry(Path(args.var_dir) / "registry.db")
    paper = PaperPortfolio(cash=float(args.paper_cash))
    state = caps_mod.CapsState()

    def quote(symbol: str) -> float:
        return float(md.prices(client, [symbol])[0].lastPrice)

    gate = Gate(registry, CostModel.from_config(costs_cfg),
                live_trading=False,  # Phase 7 전까지 무조건 페이퍼 (IMPL-07)
                paper=paper, quotes=quote,
                receipt_ttl_s=float(tg_cfg.get("confirm_ttl_s", 300)))
    watcher = Watcher(
        registry=registry, caps_state=state,
        config=WatcherConfig.from_runtime_yaml(args.runtime),
        positions=lambda: {},
    )
    from quantbot.engine.watcher import restore_hold_state

    if restore_hold_state(registry, state):
        print("⚠ 이전 fail-safe hold가 해제되지 않은 채 재시작 — hold 상태로 기동 (RISK-06)")
    tg = TelegramClient.from_token_file(
        tg_cfg["token_path"], timeout_s=10.0,
        poll_timeout_s=float(tg_cfg.get("poll_timeout_s", 25)),
    )
    router = Router(
        owner_chat_id=owner,
        tier1={
            "/status": lambda a: f"hold={state.hold} cb={state.cb_tripped} "
                                 f"주문 {state.daily_order_count}회",
            "/positions": lambda a: str(dict(paper.qty)) or "(없음)",
            "/pnl": lambda a: f"현금 {paper.cash:,.0f}",
            "/report": lambda a: "아침 보고서는 리밸런싱 사이클이 발신",
            "/strategy": lambda a: "registry 조회는 /status 참조",
            "/switch": lambda a: "자동 승인 파이프라인 미배선 — TODO.md",
            "/pause": lambda a: (setattr(state, "hold", True), "일시 중단")[-1],
        },
        tier2_preview={
            "/resume": lambda a: f"hold 해제 preview — 현재 hold={state.hold}",
            "/order": lambda a: "수동 주문 preview 미배선 — TODO.md",
            "/cb-release": lambda a: f"CB={state.cb_tripped}",
            "/promote": lambda a: "⚠ 미검증 강제 승격 preview — TODO.md",
        },
        tier2_execute={
            "/resume": lambda a: (watcher.release_hold(
                confirmed_by_tier2=True, detail=a or "/resume"), "재개 완료")[-1],
            "/order": lambda a: "미배선",
            "/cb-release": lambda a: (setattr(state, "cb_tripped", False), "CB 해제")[-1],
            "/promote": lambda a: "미배선",
        },
        tokens=TokenStore(ttl_s=float(tg_cfg.get("confirm_ttl_s", 300))),
    )
    print(f"페이퍼 운영 시작 — 명령 {sorted(ROUTING)} (중단: Ctrl-C)")
    offset = None
    try:
        while True:
            offset = poll_once(tg, router.handle, offset)
            watcher.check_heartbeat()
    except KeyboardInterrupt:
        registry.close()
        return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="quantbot")
    parser.add_argument("--runtime", default=DEFAULT_RUNTIME,
                        help="runtime.yaml 경로 (기본: config/runtime.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_verify = sub.add_parser("api-verify", help="공식 API 계약 실측 검증")
    p_verify.add_argument("--symbols", default="005930,AAPL",
                          help="검증에 쓸 종목 (CSV)")
    p_verify.add_argument("--account", action="store_true",
                          help="계좌 스코프 조회(읽기 전용)까지 검증")
    p_verify.set_defaults(fn=cmd_api_verify)

    p_flows = sub.add_parser("collect-flows", help="KR flows 일일 적재 (IMPL-06)")
    p_flows.add_argument("--symbols", default="", help="추가 종목 (CSV)")
    p_flows.add_argument("--screener", default="", help="tossctl 스크리너 프리셋")
    p_flows.add_argument("--var-dir", default="var", help="registry.db/flows.db 위치")
    p_flows.set_defaults(fn=cmd_collect_flows)

    p_fetch = sub.add_parser("fetch-candles", help="일봉 CSV 적재 (백테스트 데이터)")
    p_fetch.add_argument("--symbols", required=True, help="종목 CSV")
    p_fetch.add_argument("--days", type=int, default=1800, help="종목당 일봉 수 (5년+)")
    p_fetch.add_argument("--out", default="var/data/candles.csv")
    p_fetch.set_defaults(fn=cmd_fetch_candles)

    p_vix = sub.add_parser("import-vix", help="CBOE VIX CSV를 candles CSV에 병합")
    p_vix.add_argument("--data", required=True, help="fetch-candles가 만든 CSV")
    p_vix.add_argument("--cboe-csv", required=True, help="CBOE VIX_History.csv")
    p_vix.add_argument("--vix-symbol", default="VIX")
    p_vix.add_argument("--out", required=True, help="병합 결과 CSV")
    p_vix.set_defaults(fn=cmd_import_vix)

    p_bt = sub.add_parser("backtest", help="사전등록·게이트 판정 백테스트 1회")
    p_bt.add_argument("--strategy", default="strategies/momentum-core.v1.yaml")
    p_bt.add_argument("--grid", default="config/grids/momentum-core.yaml")
    p_bt.add_argument("--config", default="config/backtest.yaml")
    p_bt.add_argument("--gates", default="config/backtest_gates.yaml")
    p_bt.add_argument("--data", required=True, help="fetch-candles가 만든 CSV")
    p_bt.add_argument("--start", default="", help="데이터 범위 시작일 (기본: 전체)")
    p_bt.add_argument("--end", default="", help="데이터 범위 종료일")
    p_bt.add_argument("--index-symbol", default="", help="레짐 지수 데이터 열 (예: SPX)")
    p_bt.add_argument("--vix-symbol", default="", help="VIX 데이터 열")
    p_bt.add_argument("--allow-no-regime", action="store_true",
                      help="레짐 없이 판정 (H1 검증 불가 — 명시적 확인)")
    p_bt.add_argument("--var-dir", default="var")
    p_bt.set_defaults(fn=cmd_backtest)

    p_run = sub.add_parser("run", help="페이퍼 운영 루프 (텔레그램 필요)")
    p_run.add_argument("--var-dir", default="var")
    p_run.add_argument("--paper-cash", default="5000000")
    p_run.set_defaults(fn=cmd_run)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
