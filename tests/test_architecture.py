"""아키텍처 테스트 — §I1 표의 계층 규칙을 AST로 집행한다 (IMPL-02 장치 2).

이 테스트는 모든 커밋의 문지기다: 위반 = CI 빨강 = 머지 불가.

집행 규칙
  1. 계층 import 방향 (§I1 표): 명령은 아래로만.
  2. subprocess import는 quantbot.adapter.proc 에서만 (ARCH-02).
  3. "invariants.yaml" 문자열 리터럴은 quantbot.engine.invariants 에서만 (ISO-01).
  4. quantbot.adapter.order import는 quantbot.engine.gate 에서만 (게이트 우회 불가).
  5. quantbot.strategy.translator 는 봇 본체 어디서도 import되지 않는다 (ISO-02).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
PKG_ROOT = SRC / "quantbot"

# ── §I1 표: 패키지 → import 허용 prefix (자기 자신은 항상 허용) ──────────
LAYER_RULES: dict[str, tuple[str, ...]] = {
    # Interface → engine만 (명령 큐·보고 조회). adapter·strategy 직접 import 금지.
    "quantbot.interface": ("quantbot.engine",),
    # Strategy → 없음. 순수 함수 + 엔진이 주입하는 데이터 뷰.
    "quantbot.strategy": (),
    # 엔진 → adapter, strategy(해석 대상으로).
    "quantbot.engine": ("quantbot.adapter", "quantbot.strategy"),
    # 어댑터 → subprocess(tossctl)만. 프로젝트 내 상위 패키지 import 금지.
    "quantbot.adapter": (),
    # 비계층 도구 → adapter 읽기 표면, strategy.slots, engine.registry(아티팩트 append).
    # engine.gate·adapter.order 접근 금지 — 규칙 1(order는 규칙 4)이 함께 막는다.
    "quantbot.backtest": ("quantbot.adapter", "quantbot.strategy.slots", "quantbot.engine.registry"),
    "quantbot.collect": ("quantbot.adapter", "quantbot.strategy.slots", "quantbot.engine.registry"),
}

# 순수 리프 유틸 — 어떤 계층도 아니며 누구나 import 가능 (stdlib 대용 YAML 파서).
ALWAYS_ALLOWED: tuple[str, ...] = ("quantbot._yaml",)

# 조립 루트 — 전 계층을 이어 붙이는 유일한 최상위 모듈. 규칙 2·3·4·5는 여전히 적용.
COMPOSITION_ROOTS: frozenset[str] = frozenset({"quantbot.cli"})

# 규칙 2·4·5: 모듈 → 그 모듈을 import할 수 있는 유일한 모듈들
EXCLUSIVE_IMPORTERS: dict[str, frozenset[str]] = {
    "subprocess": frozenset({"quantbot.adapter.proc"}),
    "quantbot.adapter.order": frozenset({"quantbot.engine.gate"}),
    "quantbot.strategy.translator": frozenset(),  # ISO-02: 별도 프로세스로만
}

# 규칙 3: 경로 문자열 → 허용 모듈
EXCLUSIVE_PATH_LITERALS: dict[str, frozenset[str]] = {
    "invariants.yaml": frozenset({"quantbot.engine.invariants"}),
}


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _iter_modules():
    for path in sorted(PKG_ROOT.rglob("*.py")):
        name = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        yield name, path, tree


def _imports_of(tree: ast.AST, module: str) -> set[str]:
    """모듈이 import하는 절대 모듈명 집합 (상대 import는 절대명으로 해석)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                parts = module.split(".")
                # 모듈 자신은 패키지가 아니므로 level=1이 부모 패키지를 가리킨다
                anchor = parts[: len(parts) - node.level]
                base = ".".join(anchor + ([node.module] if node.module else []))
            if base:
                found.add(base)
                for alias in node.names:
                    found.add(f"{base}.{alias.name}")
    return found


def _layer_of(module: str) -> str | None:
    for layer in LAYER_RULES:
        if module == layer or module.startswith(layer + "."):
            return layer
    return None


def _is_within(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


ALL_MODULES = [(name, path, tree) for name, path, tree in _iter_modules()]


def test_tree_is_not_empty():
    """빈 트리의 공허한 통과(vacuous pass)를 차단한다."""
    names = {name for name, _, _ in ALL_MODULES}
    assert len(names) >= 40, f"모듈 트리가 비정상적으로 작다: {len(names)}"
    for layer in LAYER_RULES:
        assert any(_is_within(n, layer) for n in names), f"계층 부재: {layer}"


def test_every_module_is_covered_by_a_rule():
    """계층 규칙의 사각지대 금지 — 새 패키지는 이 표에 등록해야만 존재할 수 있다."""
    for name, path, _ in ALL_MODULES:
        if name in COMPOSITION_ROOTS or name in ALWAYS_ALLOWED or name == "quantbot":
            continue
        assert _layer_of(name) is not None, (
            f"{name} ({path})가 §I1 계층 표 어디에도 속하지 않는다 — "
            "LAYER_RULES에 자리를 정해야 한다"
        )


def test_layer_import_directions():
    """§I1 표: 명령은 아래로만 흐른다."""
    violations: list[str] = []
    for name, path, tree in ALL_MODULES:
        layer = _layer_of(name)
        if layer is None:
            continue  # cli/_yaml/루트 — 별도 규칙으로 커버
        allowed = LAYER_RULES[layer]
        for imported in _imports_of(tree, name):
            if not _is_within(imported, "quantbot"):
                continue  # stdlib·서드파티는 계층 규칙 대상 아님 (subprocess는 별도)
            if _is_within(imported, layer):
                continue
            if imported == "quantbot":
                continue
            if any(_is_within(imported, a) for a in ALWAYS_ALLOWED):
                continue
            if any(_is_within(imported, a) for a in allowed):
                continue
            violations.append(f"{name} → {imported}  ({path})")
    assert not violations, "계층 방향 위반 (§I1):\n" + "\n".join(violations)


def test_exclusive_importers():
    """subprocess/adapter.order/translator — 지정 모듈 밖에서 import 금지."""
    violations: list[str] = []
    for name, path, tree in ALL_MODULES:
        imports = _imports_of(tree, name)
        for target, allowed in EXCLUSIVE_IMPORTERS.items():
            if name in allowed or _is_within(name, target):
                continue
            hits = [i for i in imports if _is_within(i, target)]
            for hit in hits:
                violations.append(f"{name} → {hit}  ({path})")
    assert not violations, "전용 import 규칙 위반 (IMPL-02):\n" + "\n".join(violations)


def test_invariants_path_literal_is_exclusive():
    """"invariants.yaml" 경로 문자열은 engine/invariants.py 밖에 존재할 수 없다 (ISO-01)."""
    violations: list[str] = []
    for name, path, tree in ALL_MODULES:
        for literal, allowed in EXCLUSIVE_PATH_LITERALS.items():
            if name in allowed:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and literal in node.value
                ):
                    violations.append(
                        f"{name}:{node.lineno} 문자열 {node.value!r}  ({path})"
                    )
    assert not violations, "invariants 경로 접근 위반 (ISO-01):\n" + "\n".join(violations)


def test_composition_root_still_obeys_exclusive_rules():
    """cli는 계층 표에서 면제되지만 subprocess·order·invariants 규칙은 그대로 적용된다."""
    for name, path, tree in ALL_MODULES:
        if name not in COMPOSITION_ROOTS:
            continue
        imports = _imports_of(tree, name)
        for target, allowed in EXCLUSIVE_IMPORTERS.items():
            assert name in allowed or not any(
                _is_within(i, target) for i in imports
            ), f"{name}이 {target}을 import한다 ({path})"
