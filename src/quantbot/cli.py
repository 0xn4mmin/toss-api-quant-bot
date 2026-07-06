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

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
