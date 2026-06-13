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
