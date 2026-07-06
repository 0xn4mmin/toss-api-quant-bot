"""DoD: doctor·quote get 라운드트립(가짜 바이너리) + 계약 위반 주입 → SchemaDrift 전체 거부."""

from __future__ import annotations

import json
import shutil

import pytest

from quantbot.adapter import acct, health, ledger, md, mkt
from quantbot.adapter.contracts import SchemaDriftError
from quantbot.adapter.proc import TossctlRunner
from conftest import TOSSCTL_FIXTURES, make_run_policy


def test_doctor_roundtrip(tossctl_runner):
    """DoD: doctor 라운드트립 — subprocess → JSON → 계약 검증 → typed 객체."""
    report = health.doctor(tossctl_runner)
    assert report.ok is True
    assert [c.name for c in report.checks] == ["auth", "network"]


def test_quote_get_roundtrip(tossctl_runner):
    """DoD: quote get 라운드트립."""
    q = md.quote_get(tossctl_runner, "AAPL")
    assert (q.symbol, q.currency) == ("AAPL", "USD")
    assert q.price == pytest.approx(213.55)


def test_every_query_surface_parses_its_fixture(tossctl_runner):
    """전 조회 함수가 픽스처를 typed 객체로 반환한다 — 표면 전수 검사."""
    assert health.auth_status(tossctl_runner).authenticated is True
    assert len(md.quote_batch(tossctl_runner, ["AAPL", "MSFT"]).quotes) == 2
    assert md.chart(tossctl_runner, "AAPL", "day", 3).bars[-1].close == pytest.approx(213.55)
    assert md.flows(tossctl_runner, "005930").rows[0].foreign_net > 0
    ob = md.orderbook(tossctl_runner, "005930")
    assert ob.bids[0].price < ob.asks[0].price
    assert md.warnings(tossctl_runner, "005930").flags == []
    assert md.commission(tossctl_runner).rate == pytest.approx(0.00015)
    assert md.limits(tossctl_runner, "AAPL").fractional is True
    assert mkt.index(tossctl_runner, "SPX").value > 0
    assert mkt.fx(tossctl_runner, "USDKRW").pair == "USDKRW"
    assert mkt.hours(tossctl_runner, "us").is_open is False
    assert len(mkt.screener(tossctl_runner, "kr_flows").rows) == 2
    assert acct.summary(tossctl_runner).cash == pytest.approx(5_000_000.0)
    assert acct.positions(tossctl_runner).positions[0].symbol == "AAPL"
    assert ledger.orders_list(tossctl_runner).items[0].status == "filled"
    assert ledger.transactions_list(tossctl_runner).items[0].side == "buy"


# ── DoD: 계약을 깨는 응답 픽스처 주입 → 부분 파싱 없이 전체 거부 ────────


def _mutate(obj: dict, kind: str) -> dict:
    m = json.loads(json.dumps(obj))
    if kind == "extra_field":
        m["surprise"] = 1
    elif kind == "missing_field":
        del m["price"]
    elif kind == "wrong_type":
        m["price"] = "213.55"          # 숫자 → 문자열
    elif kind == "renamed_field":
        m["last_price"] = m.pop("price")
    return m


@pytest.mark.parametrize("kind", ["extra_field", "missing_field", "wrong_type", "renamed_field"])
def test_contract_violation_is_rejected_whole(kind, tmp_path, monkeypatch):
    """필드 하나 변조 → SchemaDriftError. 부분 객체는 존재하지 않는다 (fail-closed)."""
    drift_dir = tmp_path / "fx"
    shutil.copytree(TOSSCTL_FIXTURES, drift_dir)
    original = json.loads((drift_dir / "quote_get.json").read_text(encoding="utf-8"))
    (drift_dir / "quote_get.json").write_text(
        json.dumps(_mutate(original, kind)), encoding="utf-8"
    )
    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(drift_dir))
    runner = TossctlRunner(make_run_policy())
    with pytest.raises(SchemaDriftError) as exc:
        md.quote_get(runner, "AAPL")
    drift = exc.value.drift
    assert drift.command[:2] == ("quote", "get")
    assert drift.model == "Quote"


def test_schema_drift_carries_signal_payload(tmp_path, monkeypatch):
    """SchemaDrift는 예외이자 상향 보고용 신호다 — 엔진이 소비할 payload를 가진다."""
    drift_dir = tmp_path / "fx"
    shutil.copytree(TOSSCTL_FIXTURES, drift_dir)
    (drift_dir / "doctor.json").write_text('{"ok": "yes", "checks": []}', encoding="utf-8")
    monkeypatch.setenv("FAKE_TOSSCTL_FIXTURES", str(drift_dir))
    runner = TossctlRunner(make_run_policy())
    with pytest.raises(SchemaDriftError) as exc:
        health.doctor(runner)
    assert exc.value.drift.command == ("doctor",)
    assert "ok" in exc.value.drift.detail


def test_contracts_are_frozen(tossctl_runner):
    """typed 객체는 생성 후 불변 — 어댑터 밖에서 응답을 조작할 수 없다."""
    q = md.quote_get(tossctl_runner, "AAPL")
    with pytest.raises(Exception):
        q.price = 0.0
