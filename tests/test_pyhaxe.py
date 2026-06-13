"""
Smoke tests for PyHaxe.

These are pragmatic regression checks, not exhaustive coverage. They
verify that the checker and emitter produce expected results on the
example files. If any of these break, it means a recent change has
regressed one of the milestones the project depends on.

Run with: pytest
"""

import subprocess
import sys
from pathlib import Path

from pyhaxe.discipline_checker import check_file
from pyhaxe.haxe_emitter import convert


REPO_ROOT = Path(__file__).parent.parent
EXAMPLES = REPO_ROOT / "examples"


# ============================================================
# Discipline checker
# ============================================================

def test_checker_passes_on_disciplined_examples():
    for filename in ["basic_example.py", "inventory_example.py",
                     "classes_example.py", "collections_example.py",
                     "visibility_example.py", "kwargs_example.py",
                     "exceptions_example.py", "types_example.py"]:
        violations = check_file(str(EXAMPLES / filename))
        assert violations == [], (
            filename + " should be clean but has " +
            str(len(violations)) + " violation(s): " +
            ", ".join(v.kind for v in violations)
        )


def test_checker_catches_violations_in_bad_example():
    violations = check_file(str(EXAMPLES / "bad_example.py"))
    kinds = {v.kind for v in violations}

    expected_kinds = {
        "multiple-inheritance",
        "missing-return-annotation",
        "missing-param-annotation",
        "with-statement",
        "tuple-unpacking",
        "lambda",
        "generator-expression",
        "varargs",
        "kwargs-param",
        "yield",
        "try-finally",
        "try-else",
        "bare-raise",
    }

    missing = expected_kinds - kinds
    assert not missing, "checker missed violations: " + ", ".join(sorted(missing))


# ============================================================
# Haxe emitter
# ============================================================

def test_emitter_produces_expected_output_for_basic_example():
    _check_golden("basic_example")


def test_emitter_produces_expected_output_for_classes_example():
    _check_golden("classes_example")


def test_emitter_produces_expected_output_for_collections_example():
    _check_golden("collections_example")


def test_emitter_produces_expected_output_for_visibility_example():
    _check_golden("visibility_example")


def test_emitter_produces_expected_output_for_kwargs_example():
    _check_golden("kwargs_example")


def test_emitter_produces_expected_output_for_exceptions_example():
    _check_golden("exceptions_example")


def test_emitter_produces_expected_output_for_types_example():
    _check_golden("types_example")


def test_emitter_produces_expected_output_for_inventory_example():
    _check_golden("inventory_example")


def _check_golden(stem):
    py_path = EXAMPLES / (stem + ".py")
    hx_path = EXAMPLES / (stem + ".hx")

    source = py_path.read_text()
    actual = convert(source, str(py_path)).strip()
    expected = hx_path.read_text().strip()

    assert actual == expected, (
        "emitter output for " + py_path.name +
        " has diverged from the checked-in golden file. " +
        "If the change is intentional, regenerate the .hx file with " +
        "`pyhaxe-emit examples/" + stem + ".py > examples/" + stem + ".hx`."
    )


# ============================================================
# Type-directed truthiness (regression: positive string truthiness)
# ============================================================

def test_positive_string_truthiness_on_field_is_coerced():
    # `if self.prop:` where prop is a str must become a non-null/non-empty
    # test in Haxe, not a bare `if (prop)` (which drops the value / is a
    # type error). Regression for the serialize() prop-dropping bug.
    source = (
        "class RefExpr:\n"
        "    prop: str\n"
        "    def __init__(self, prop: str) -> None:\n"
        "        self.prop = prop\n"
        "    def show(self) -> str:\n"
        "        if self.prop:\n"
        "            return self.prop\n"
        "        return \"\"\n"
    )
    out = convert(source, "<test>")
    assert "this.prop != null && this.prop.length > 0" in out, out


def test_positive_string_truthiness_via_downcast_is_coerced():
    # Base-typed receiver whose str field lives on a subclass: the access
    # is routed through `(cast x)` and still needs truthiness coercion.
    source = (
        "class Expr:\n"
        "    kind: str\n"
        "    def __init__(self, kind: str) -> None:\n"
        "        self.kind = kind\n"
        "\n"
        "class RefExpr(Expr):\n"
        "    prop: str\n"
        "    def __init__(self, prop: str) -> None:\n"
        "        super().__init__(\"ref\")\n"
        "        self.prop = prop\n"
        "\n"
        "def serialize(node: Expr) -> str:\n"
        "    if node.prop:\n"
        "        return node.prop\n"
        "    return \"\"\n"
    )
    out = convert(source, "<test>")
    assert "(cast node).prop != null && (cast node).prop.length > 0" in out, out


# ============================================================
# Comprehensions / collections (Round 2)
# ============================================================

def test_list_comprehension_with_filter():
    source = (
        "def f(xs: list) -> list:\n"
        "    return [x for x in xs if x > 0]\n"
    )
    out = convert(source, "<test>")
    assert "[for (x in xs) if (x > 0) x]" in out, out


def test_dict_comprehension_builds_map():
    source = (
        "def f(xs: list) -> dict:\n"
        "    return {k: 0 for k in xs}\n"
    )
    out = convert(source, "<test>")
    assert "new Map()" in out and ".set(k, 0)" in out, out


def test_dict_get_with_default():
    source = (
        "def f(m: dict, k: str) -> int:\n"
        "    return m.get(k, 0)\n"
    )
    out = convert(source, "<test>")
    assert "m.exists(k) ? m.get(k) : 0" in out, out


def test_sorted_with_key():
    source = (
        "def keyf(x: int) -> int:\n"
        "    return x\n"
        "def f(xs: list) -> list:\n"
        "    return sorted(xs, key=keyf)\n"
    )
    out = convert(source, "<test>")
    assert ".copy()" in out and ".sort(" in out and "Reflect.compare(keyf(" in out, out


def test_max_two_args_and_iterable():
    source = (
        "def f(a: float, b: float, xs: list) -> float:\n"
        "    c: float = max(a, b)\n"
        "    d = max(xs)\n"
        "    return c + d\n"
    )
    out = convert(source, "<test>")
    assert "Math.max(a, b)" in out, out
    assert "Lambda.fold(xs" in out, out


def test_set_type_and_membership():
    source = (
        "def f(k: str) -> bool:\n"
        "    seen: set = set()\n"
        "    seen.add(k)\n"
        "    return k in seen\n"
    )
    out = convert(source, "<test>")
    assert "new Map()" in out, out
    assert "seen.set(k, true)" in out, out
    assert "seen.exists(k)" in out, out


def test_map_values_iteration():
    source = (
        "def f(m: dict) -> int:\n"
        "    total = 0\n"
        "    for v in m.values():\n"
        "        total = total + v\n"
        "    return total\n"
    )
    out = convert(source, "<test>")
    # Iterating a Haxe Map yields its values directly.
    assert "for (v in m)" in out, out


def test_del_map_and_list():
    source = (
        "def f(xs: list, m: dict, k: str) -> None:\n"
        "    del xs[2]\n"
        "    del m[k]\n"
    )
    out = convert(source, "<test>")
    assert "xs.splice(2, 1);" in out, out
    assert "m.remove(k);" in out, out


def test_dict_items_iteration_keyvalue():
    source = (
        "def f(m: dict) -> int:\n"
        "    total = 0\n"
        "    for k, v in m.items():\n"
        "        total = total + v\n"
        "    return total\n"
    )
    out = convert(source, "<test>")
    # Haxe key-value iteration; no `.items()` method exists.
    assert "for (k => v in m)" in out, out
    assert ".items()" not in out, out


def test_class_variable_becomes_static():
    source = (
        "class C:\n"
        "    LABELS = [\"a\", \"b\"]\n"
        "    def first(self) -> str:\n"
        "        return self.LABELS[0]\n"
    )
    out = convert(source, "<test>")
    assert "static var LABELS" in out, out
    # Instance access to a static is rewritten to ClassName.NAME.
    assert "C.LABELS[0]" in out, out


def test_or_value_selection_for_objects():
    source = (
        "def f(m: dict, a: str, b: str) -> str:\n"
        "    return m.get(a) or m.get(b)\n"
    )
    out = convert(source, "<test>")
    # Python `or` on values yields an operand (ternary), not a Bool `||`.
    assert "?" in out and "||" not in out, out


def test_not_on_nullable_object_is_null_check():
    source = (
        "from typing import Dict\n"
        "class N:\n"
        "    x: int\n"
        "    def __init__(self, x: int) -> None:\n"
        "        self.x = x\n"
        "def f(m: Dict[str, N], k: str) -> bool:\n"
        "    n = m.get(k)\n"
        "    if not n:\n"
        "        return True\n"
        "    return False\n"
    )
    out = convert(source, "<test>")
    # `not n` on a tracked nullable (Dict value is a class) -> explicit null
    # test, not `!n`.
    assert "n == null" in out, out


def test_setattr_getattr_to_reflect():
    source = (
        "def f(o: object, name: str, v: str) -> None:\n"
        "    setattr(o, name, v)\n"
    )
    out = convert(source, "<test>")
    assert "Reflect.setField(o, name, v)" in out, out


def test_print_maps_to_trace():
    source = (
        "def f(x: str) -> None:\n"
        "    print(x)\n"
    )
    out = convert(source, "<test>")
    assert "trace(x)" in out, out


# ============================================================
# CLI smoke test
# ============================================================

def test_cli_entry_points_run():
    # Both commands should exit cleanly when given no args (they print
    # usage and return 0 or 1 — we just confirm they don't crash).
    for cmd in ["pyhaxe-check", "pyhaxe-emit"]:
        result = subprocess.run([cmd], capture_output=True, text=True)
        # Either success or graceful failure, not a Python traceback.
        assert "Traceback" not in result.stderr, (
            cmd + " crashed: " + result.stderr
        )
