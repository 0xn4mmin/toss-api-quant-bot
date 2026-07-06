"""엄격한 YAML 서브셋 파서 — stdlib 전용 (IMPL-01: 의존성은 pydantic·numpy 둘뿐).

지원: 들여쓰기 기반 중첩 매핑, 스칼라 리스트(`- item`), 스칼라(int/float/bool/null/str,
따옴표 문자열), `#` 주석. 그 외 구문(anchor, alias, flow collection, 멀티라인 블록,
다중 문서, 탭 들여쓰기)은 조용히 오해석하지 않고 YamlSubsetError로 거부한다.

계층에 속하지 않는 순수 리프 유틸리티 — 어떤 quantbot 패키지도 import하지 않는다.
"""

from __future__ import annotations


class YamlSubsetError(ValueError):
    """지원하지 않는 YAML 구문 또는 형식 오류."""

    def __init__(self, lineno: int, message: str) -> None:
        super().__init__(f"line {lineno}: {message}")
        self.lineno = lineno


_FORBIDDEN_PREFIXES = ("&", "*", "{", "[", "|", ">", "%", "---", "..." )


def _strip_comment(line: str, lineno: int) -> str:
    out: list[str] = []
    quote: str | None = None
    for ch in line:
        if quote is None:
            if ch == "#":
                break
            if ch in ("'", '"'):
                quote = ch
            out.append(ch)
        else:
            out.append(ch)
            if ch == quote:
                quote = None
    if quote is not None:
        raise YamlSubsetError(lineno, "닫히지 않은 따옴표")
    return "".join(out).rstrip()


def _parse_scalar(raw: str, lineno: int) -> object:
    raw = raw.strip()
    if raw.startswith(_FORBIDDEN_PREFIXES):
        raise YamlSubsetError(lineno, f"지원하지 않는 구문: {raw[:10]!r}")
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    if ("'" in raw) or ('"' in raw):
        raise YamlSubsetError(lineno, "따옴표는 값 전체를 감싸야 한다")
    low = raw.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", "none", ""):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _handle_mapping_line(
    content: str, indent: int, container: dict, lineno: int
) -> tuple[int, dict, str] | None:
    """"key: value" 또는 "key:" 처리 — 후자는 pending_key를 반환한다."""
    key, sep, value = content.partition(":")
    if not sep:
        raise YamlSubsetError(lineno, f"매핑도 리스트도 아닌 줄: {content!r}")
    if value and not value.startswith(" "):
        raise YamlSubsetError(lineno, "':' 뒤에는 공백이 필요하다")
    key = key.strip()
    if not key:
        raise YamlSubsetError(lineno, "빈 키")
    if key in container:
        raise YamlSubsetError(lineno, f"중복 키: {key!r}")
    if value.strip():
        container[key] = _parse_scalar(value, lineno)
        return None
    return (indent, container, key)


LIST_ITEM_CHILD_INDENT = 2  # "- key: ..." 항목의 하위 키 가상 들여쓰기 (표준 YAML)


def loads(text: str) -> dict[str, object]:
    """YAML 서브셋 문자열을 dict로 파싱한다. 최상위는 매핑이어야 한다."""
    root: dict[str, object] = {}
    # 스택: (indent, container). 컨테이너는 dict 또는 list.
    stack: list[tuple[int, object]] = [(0, root)]
    pending_key: tuple[int, dict, str] | None = None  # 값 없는 "key:" — 자식 대기

    for lineno, rawline in enumerate(text.splitlines(), start=1):
        if "\t" in rawline:
            raise YamlSubsetError(lineno, "탭 들여쓰기는 지원하지 않는다")
        line = _strip_comment(rawline, lineno)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if content.startswith(("---", "...")):
            raise YamlSubsetError(lineno, "다중 문서는 지원하지 않는다")

        # pending_key가 있고 이번 줄이 더 깊으면 자식 컨테이너를 연다
        if pending_key is not None:
            pk_indent, pk_dict, pk_key = pending_key
            if indent > pk_indent:
                child: object = [] if content.startswith("- ") or content == "-" else {}
                pk_dict[pk_key] = child
                stack.append((indent, child))
            else:
                pk_dict[pk_key] = None  # 값도 자식도 없는 키
            pending_key = None

        # 현재 들여쓰기에 맞는 컨테이너로 되돌아간다
        while stack and indent < stack[-1][0]:
            stack.pop()
        if not stack or indent != stack[-1][0]:
            raise YamlSubsetError(lineno, "들여쓰기가 상위 레벨과 정렬되지 않는다")
        container = stack[-1][1]

        if content.startswith("- ") or content == "-":
            if not isinstance(container, list):
                raise YamlSubsetError(lineno, "리스트 항목이 매핑 위치에 있다")
            item = content[1:].strip()
            if not item:
                raise YamlSubsetError(lineno, "빈 리스트 항목")
            if item.startswith("- "):
                raise YamlSubsetError(lineno, "중첩 리스트는 지원하지 않는다")
            is_quoted = item[0] in ("'", '"')
            if not is_quoted and (item.endswith(":") or ": " in item):
                # 매핑 항목 ("- key: ..." / "- key:") — 하위 키는 indent+2 레벨
                d: dict[str, object] = {}
                container.append(d)
                child_indent = indent + LIST_ITEM_CHILD_INDENT
                stack.append((child_indent, d))
                pending_key = _handle_mapping_line(item, child_indent, d, lineno)
                continue
            container.append(_parse_scalar(item, lineno))
            continue

        if not isinstance(container, dict):
            raise YamlSubsetError(lineno, "매핑 키가 리스트 위치에 있다")
        pending_key = _handle_mapping_line(content, indent, container, lineno)

    if pending_key is not None:
        pending_key[1][pending_key[2]] = None
    return root


def load_file(path: str) -> dict[str, object]:
    with open(path, encoding="utf-8") as f:
        return loads(f.read())
