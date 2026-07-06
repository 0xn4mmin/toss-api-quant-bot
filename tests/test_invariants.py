"""invariants 로더 검사 (IMPL-04, ISO-01) — frozen dataclass, 부분 로드 없음."""

from __future__ import annotations

import dataclasses

import pytest

from quantbot.engine.invariants import Invariants, InvariantsError, load_invariants

VALID = """\
version: 1
position:
  max_weight_pct: 12
  max_leverage: 0
universe:
  whitelist_only: true
  kr_path: "universe/kr.yaml"
  us_path: "universe/us.yaml"
  exclude_leveraged_etf: true
backtest:
  max_mdd_pct: 15
rebalance:
  min_interval_days: 7
turnover:
  auto_approve_max_pct: 50
circuit_breaker:
  daily_loss_pct: 3
lifecycle:
  auto_approve_requires_paper: true
orders:
  daily_max_count: 10
  per_order_max_amount_krw: 1000000
"""


@pytest.fixture
def valid_file(tmp_path):
    p = tmp_path / "inv.yaml"
    p.write_text(VALID, encoding="utf-8")
    return p


def test_repo_config_file_loads():
    """저장소에 실제로 존재하는 config 파일이 로드된다."""
    inv = load_invariants()
    assert isinstance(inv, Invariants)
    assert inv.position.max_leverage == 0  # INV-02


def test_loads_valid_file(valid_file):
    inv = load_invariants(valid_file)
    assert inv.position.max_weight_pct == 12  # INV-01
    assert inv.rebalance.min_interval_days == 7  # INV-05
    assert inv.orders.daily_max_count == 10  # INV-09


def test_result_is_deeply_frozen(valid_file):
    """로드 후 변경 불가 — 어떤 코드도 불변식을 런타임에 완화할 수 없다."""
    inv = load_invariants(valid_file)
    with pytest.raises(dataclasses.FrozenInstanceError):
        inv.version = 2
    with pytest.raises(dataclasses.FrozenInstanceError):
        inv.position.max_weight_pct = 99
    with pytest.raises(dataclasses.FrozenInstanceError):
        inv.orders.per_order_max_amount_krw = 10**12


def test_missing_file_is_error(tmp_path):
    with pytest.raises(InvariantsError, match="파일이 없다"):
        load_invariants(tmp_path / "nope.yaml")


@pytest.mark.parametrize(
    ("needle", "replacement", "match"),
    [
        ("version: 1", "version: 2", "version"),
        ("  max_weight_pct: 12\n", "", "max_weight_pct"),  # 필드 누락 = 거부
        ("max_weight_pct: 12", "max_weight_pct: 120", "범위"),
        ("max_leverage: 0", "max_leverage: 2", "범위"),  # INV-02: 0 외엔 로드 불가
        ("whitelist_only: true", "whitelist_only: maybe", "bool"),
        ("min_interval_days: 7", "min_interval_days: 0", "이상"),
        ("daily_max_count: 10", "daily_max_count: ten", "정수"),
    ],
)
def test_invalid_values_are_rejected(tmp_path, needle, replacement, match):
    text = VALID.replace(needle, replacement)
    assert text != VALID, "테스트 자체 오류: 치환이 적용되지 않았다"
    p = tmp_path / "inv.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(InvariantsError, match=match):
        load_invariants(p)


def test_inv11_field_loads(valid_file):
    inv = load_invariants(valid_file)
    assert inv.universe.exclude_leveraged_etf is True  # INV-11


def test_inv11_leverage_verdict():
    """INV-11 판정 술어 — 하나의 규칙이 레버리지·인버스를 모두 잡고,
    null은 유형에 따라 '정상'과 '판정 불가'로 갈린다 (2026-07-06 소유자 결정)."""
    from quantbot.engine.invariants import (
        VERDICT_ELIGIBLE,
        VERDICT_INDETERMINATE,
        VERDICT_REJECTED,
        leverage_verdict,
    )

    # 일반 주식: null이 명세상 정상값
    assert leverage_verdict("STOCK", None) == VERDICT_ELIGIBLE
    assert leverage_verdict("FOREIGN_STOCK", None) == VERDICT_ELIGIBLE
    # ETF/ETN 계열: 1.0만 통과
    assert leverage_verdict("ETF", "1.0") == VERDICT_ELIGIBLE
    assert leverage_verdict("FOREIGN_ETF", "3.0") == VERDICT_REJECTED   # TQQQ류
    assert leverage_verdict("ETN", "2.0") == VERDICT_REJECTED
    assert leverage_verdict("ETF", "-1.0") == VERDICT_REJECTED          # 인버스 포함
    # ETF/ETN인데 null = 데이터 누락 — 안전이 아니라 판정 불가 (fail-closed)
    assert leverage_verdict("FOREIGN_ETF", None) == VERDICT_INDETERMINATE
    assert leverage_verdict("ETF", "N/A") == VERDICT_INDETERMINATE
    # 비ETF인데 레버리지 값이 붙어 오면 방어적 거부
    assert leverage_verdict("STOCK", "2.0") == VERDICT_REJECTED
    assert leverage_verdict("REIT", "1.0") == VERDICT_ELIGIBLE


def test_malformed_yaml_is_rejected(tmp_path):
    p = tmp_path / "inv.yaml"
    p.write_text("version: 1\nposition: {max_weight_pct: 12}\n", encoding="utf-8")
    with pytest.raises(InvariantsError, match="파싱 실패"):
        load_invariants(p)
