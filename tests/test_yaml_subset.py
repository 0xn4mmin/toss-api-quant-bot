"""_yaml 서브셋 파서 검사 — 지원 구문은 정확히, 미지원 구문은 명시적 거부."""

from __future__ import annotations

import pytest

from quantbot import _yaml


def test_nested_mapping_scalars_and_comments():
    doc = """\
# 주석
version: 1
a:
  b: 12          # 인라인 주석
  c: 1.5
  flag: true
  none_val: null
  s: "quoted # not a comment"
  bare: hello
top: -3
"""
    assert _yaml.loads(doc) == {
        "version": 1,
        "a": {
            "b": 12,
            "c": 1.5,
            "flag": True,
            "none_val": None,
            "s": "quoted # not a comment",
            "bare": "hello",
        },
        "top": -3,
    }


def test_list_of_scalars():
    doc = """\
tickers:
  - SPY
  - QQQ
  - "005930"
"""
    assert _yaml.loads(doc) == {"tickers": ["SPY", "QQQ", "005930"]}


def test_deep_nesting_and_dedent():
    doc = """\
a:
  b:
    c: 1
  d: 2
e: 3
"""
    assert _yaml.loads(doc) == {"a": {"b": {"c": 1}, "d": 2}, "e": 3}


@pytest.mark.parametrize(
    "doc",
    [
        "a: {b: 1}",          # flow mapping
        "a: [1, 2]",          # flow list
        "a: &anchor 1",       # anchor
        "b: *anchor",         # alias
        "a: |\n  text",       # block scalar
        "---\na: 1",          # 다중 문서
        "\ta: 1",             # 탭 들여쓰기
        "a: 1\na: 2",         # 중복 키
        "a:\n  - k: v",       # 리스트 안 매핑
        'a: "unclosed',       # 닫히지 않은 따옴표
    ],
)
def test_unsupported_syntax_is_rejected(doc):
    """오해석보다 거부 — 파서가 모르는 구문을 조용히 넘기지 않는다."""
    with pytest.raises(_yaml.YamlSubsetError):
        _yaml.loads(doc)
