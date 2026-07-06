"""DoD: 동일 시드 재실행 시 결과 바이트 동일 — 완전 독립 재실행 간 아티팩트 해시 일치."""

from __future__ import annotations

from quantbot.backtest import judge, prereg, walkforward
from quantbot.backtest.data import MarketDataStore
from quantbot.engine.registry import Registry
from conftest import (
    LOOSE_GATES,
    LOW_COSTS,
    SMALL_METH,
    gentle_uptrend,
    momentum_top1,
    trading_dates,
    write_csv,
)

GRID = {"lookback": [3, 5, 8]}


def test_independent_reruns_are_byte_identical(tmp_path):
    """레지스트리·스토어를 처음부터 다시 만들어도 canonical JSON 아티팩트의
    sha256이 같다 — 부트스트랩까지 포함해 시드가 전 난수를 지배한다."""
    n = 160
    dates = trading_dates("2018-01-02", n)
    closes = gentle_uptrend(n, seed=21, symbols=("AAA", "BBB"))
    csv_path = write_csv(tmp_path / "d.csv", dates, closes)

    shas, payload_jsons = [], []
    for run in ("one", "two"):
        store = MarketDataStore.from_csv(csv_path)
        with Registry(tmp_path / f"reg_{run}.db") as reg:
            rng = (store.date(0), store.date(len(store) - 1))
            prereg.seal(reg, "repro-strat", GRID, rng,
                        walkforward.folds_spec(SMALL_METH))
            res = judge.evaluate_oos(
                reg, store, "repro-strat", GRID, rng,
                momentum_top1, LOW_COSTS, SMALL_METH, LOOSE_GATES,
            )
            shas.append(res.artifact_sha)
            art = reg.artifacts(strategy_id="repro-strat",
                                kind=judge.ARTIFACT_JUDGEMENT)[0]
            payload_jsons.append(prereg.canonical_json(art["payload"]))

    assert shas[0] == shas[1]
    assert payload_jsons[0] == payload_jsons[1]  # 바이트 동일
