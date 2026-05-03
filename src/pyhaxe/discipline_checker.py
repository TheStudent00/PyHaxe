"""
discipline_checker.py

An AST-based linter for the disciplined Python subset.

Demonstrates the no-regex parsing path: Python parses itself,
returns a tree of clean Python objects, and we walk it with
NodeVisitor. No grammar files, no regex, no parsing code.

Usage:
    python discipline_checker.py FILE [FILE ...]

This is tooling. It runs only in Python and does not need to follow
the discipline itself.
"""

import ast
import sys
from typing import List, Optional


class Violation:
    line: int
    kind: str
    message: str

    def __init__(self, line: int, kind: str, message: str) -> None:
        self.line = line
        self.kind = kind
        self.message = message

    def format(self) -> str:
        return "  line " + str(self.line) + ": [" + self.kind + "] " + self.message


class DisciplineChecker(ast.NodeVisitor):
    """Walk a Python AST and report disciplinary violations.

    Each visit_* method handles one node type. ast.NodeVisitor calls
    the appropriate visit_* method based on the node class, then we
    call self.generic_visit(node) to recurse. If we don't recurse,
    the subtree is skipped — which is exactly what we want for
    @haxe_extern wrapper classes.
    """

    violations: List[Violation]

    def __init__(self) -> None:
        self.violations = []

    def report(self, node: ast.AST, kind: str, message: str) -> None:
        line = getattr(node, "lineno", 0)
        self.violations.append(Violation(line, kind, message))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Skip wrapper classes — their bodies are manually maintained
        # per-target and exempt from disciplinary checks.
        if self._is_haxe_extern(node):
            return

        # Single inheritance only.
        if len(node.bases) > 1:
            self.report(node, "multiple-inheritance",
                        "class " + node.name + " inherits from multiple classes; only one allowed")

        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Return type annotation required (except __init__).
        if node.returns is None and node.name != "__init__":
            self.report(node, "missing-return-annotation",
                        "function " + node.name + " has no return type annotation")

        # Parameter annotations required (except self / cls).
        for arg in node.args.args:
            if arg.arg in ("self", "cls"):
                continue
            if arg.annotation is None:
                self.report(arg, "missing-param-annotation",
                            "parameter " + arg.arg + " has no type annotation")

        # No *args / **kwargs in signatures.
        if node.args.vararg is not None:
            self.report(node, "varargs",
                        "function " + node.name + " uses *" + node.args.vararg.arg + "; not allowed")
        if node.args.kwarg is not None:
            self.report(node, "kwargs-param",
                        "function " + node.name + " uses **" + node.args.kwarg.arg + "; not allowed")

        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.report(node, "lambda",
                    "lambda expressions not allowed; use named functions")
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        self.report(node, "with-statement",
                    "with statements have no Haxe equivalent; use explicit acquire/release")
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.report(node, "with-statement",
                    "async with not allowed")
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        # Haxe does not support try/finally or try/else.
        if node.finalbody:
            self.report(node, "try-finally",
                        "try/finally has no Haxe equivalent; cleanup must be done in both paths")
        if node.orelse:
            self.report(node, "try-else",
                        "try/else has no Haxe equivalent; move the else body after the try block")
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        # Bare `raise` re-raises in Python; Haxe needs an explicit value.
        if node.exc is None:
            self.report(node, "bare-raise",
                        "bare raise not allowed; use `raise e` with an explicit exception name")
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:
        self.report(node, "yield",
                    "generators / yield not allowed")
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self.report(node, "yield-from",
                    "generators / yield from not allowed")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # Tuple unpacking on the left side of =.
        for target in node.targets:
            if isinstance(target, ast.Tuple):
                self.report(target, "tuple-unpacking",
                            "tuple unpacking not allowed; assign individual variables")
        self.generic_visit(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.report(node, "generator-expression",
                    "generator expressions not allowed; build list explicitly or use a comprehension")
        self.generic_visit(node)

    def _is_haxe_extern(self, node: ast.ClassDef) -> bool:
        for dec in node.decorator_list:
            # @haxe_extern() — decorator is a Call where func is a Name
            if isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name) and dec.func.id == "haxe_extern":
                    return True
            # @haxe_extern (without parens, just a Name)
            if isinstance(dec, ast.Name) and dec.id == "haxe_extern":
                return True
        return False


def check_file(path: str) -> List[Violation]:
    f = open(path, "r")
    try:
        source = f.read()
    finally:
        f.close()
    tree = ast.parse(source, filename=path)
    checker = DisciplineChecker()
    checker.visit(tree)
    return checker.violations


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: discipline_checker.py FILE [FILE ...]")
        return 0

    total = 0
    for path in sys.argv[1:]:
        violations = check_file(path)
        if not violations:
            print(path + ": ok")
            continue
        print(path + ": " + str(len(violations)) + " violation(s)")
        for v in violations:
            print(v.format())
        total += len(violations)

    if total > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
