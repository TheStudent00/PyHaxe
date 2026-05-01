"""
haxe_emitter.py

Phase 1 milestone of the disciplined-Python -> Haxe converter.

Status (Milestone 1): handles top-level functions with typed parameters
and return types, basic expressions (literals, names, binary ops,
comparisons, boolean ops, unary ops, function calls, attribute access),
annotated/regular/augmented assignments, if/elif/else, while loops,
break, continue, pass.

Future milestones (in order):
    2. Classes — ClassDef, fields, __init__ -> new, self -> this, super
    3. Collections and iteration — for x in coll, for i in range(N), len()
    4. Wrapper handling — @haxe_extern detection, extern class emission
    5. Signature-aware kwargs resolution
    6. Try/except/raise
    7. Type system extensions — Optional, List, Dict -> Null, Array, Map
    8. Module/import system
    9. Polish — comments, formatting, error reporting

Architecture:
    HaxeEmitter walks the AST. Statement methods (stmt_X) emit lines via
    self.line(). Expression methods (expr_X) return strings. Type methods
    return strings. Dispatch uses getattr-by-name; unhandled node types
    emit a TODO comment so partial coverage is visible in the output.

Usage:
    python haxe_emitter.py FILE.py > FILE.hx
"""

import ast
import sys


# ============================================================
# Translation tables
# ============================================================

PYTHON_TO_HAXE_TYPES = {
    "int": "Int",
    "float": "Float",
    "str": "String",
    "bool": "Bool",
}

GENERIC_TYPES = {
    "List": "Array",
    "Optional": "Null",
    "Dict": "Map",
}

BINOP_MAP = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.Mod: "%",
}

COMPARE_MAP = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}

BOOLOP_MAP = {
    ast.And: "&&",
    ast.Or: "||",
}

UNARYOP_MAP = {
    ast.USub: "-",
    ast.UAdd: "+",
    ast.Not: "!",
}

AUGASSIGN_MAP = {
    ast.Add: "+=",
    ast.Sub: "-=",
    ast.Mult: "*=",
    ast.Div: "/=",
    ast.Mod: "%=",
}


# ============================================================
# Emitter
# ============================================================

class HaxeEmitter:

    def __init__(self):
        self.lines = []
        self.indent_level = 0

    def line(self, text):
        self.lines.append("    " * self.indent_level + text)

    def output(self):
        return "\n".join(self.lines)

    # === Dispatch ===

    def emit_stmt(self, node):
        method = "stmt_" + type(node).__name__
        handler = getattr(self, method, None)
        if handler is None:
            self.line("// TODO stmt: " + type(node).__name__)
            return
        handler(node)

    def emit_expr(self, node):
        method = "expr_" + type(node).__name__
        handler = getattr(self, method, None)
        if handler is None:
            return "/* TODO expr: " + type(node).__name__ + " */"
        return handler(node)

    def emit_type(self, node):
        if node is None:
            return "Void"
        if isinstance(node, ast.Name):
            return PYTHON_TO_HAXE_TYPES.get(node.id, node.id)
        if isinstance(node, ast.Subscript):
            base = node.value.id if isinstance(node.value, ast.Name) else "?"
            slice_node = node.slice
            # Python <3.9 wrapped the slice in ast.Index; handle both for safety
            if hasattr(ast, "Index") and isinstance(slice_node, ast.Index):
                slice_node = slice_node.value
            inner = self.emit_type(slice_node)
            return GENERIC_TYPES.get(base, base) + "<" + inner + ">"
        if isinstance(node, ast.Constant) and node.value is None:
            return "Void"
        return "/* TODO type */"

    # === Module ===

    def emit_module(self, node):
        for stmt in node.body:
            self.emit_stmt(stmt)

    # === Statements ===

    def stmt_ImportFrom(self, node):
        # Imports are a later milestone; skip for now.
        pass

    def stmt_Import(self, node):
        pass

    def stmt_Expr(self, node):
        # Standalone expression as statement (docstring, side-effect call).
        # Strip docstrings (string-only Expr).
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return
        self.line(self.emit_expr(node.value) + ";")

    def stmt_FunctionDef(self, node):
        params = []
        for arg in node.args.args:
            if arg.arg in ("self", "cls"):
                continue
            params.append(arg.arg + ":" + self.emit_type(arg.annotation))
        ret = self.emit_type(node.returns)
        self.line("function " + node.name + "(" + ", ".join(params) + "):" + ret + " {")
        self.indent_level += 1
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def stmt_Return(self, node):
        if node.value is None:
            self.line("return;")
            return
        self.line("return " + self.emit_expr(node.value) + ";")

    def stmt_AnnAssign(self, node):
        target = self.emit_expr(node.target)
        type_str = self.emit_type(node.annotation)
        if node.value is None:
            self.line("var " + target + ":" + type_str + ";")
            return
        value = self.emit_expr(node.value)
        self.line("var " + target + ":" + type_str + " = " + value + ";")

    def stmt_Assign(self, node):
        # Disciplined Python single-target only.
        target = self.emit_expr(node.targets[0])
        value = self.emit_expr(node.value)
        self.line(target + " = " + value + ";")

    def stmt_AugAssign(self, node):
        target = self.emit_expr(node.target)
        value = self.emit_expr(node.value)
        op = AUGASSIGN_MAP.get(type(node.op))
        if op is None:
            self.line("// TODO augassign: " + type(node.op).__name__)
            return
        self.line(target + " " + op + " " + value + ";")

    def stmt_If(self, node):
        self._emit_if_chain(node, False)

    def _emit_if_chain(self, node, is_elif):
        cond = self.emit_expr(node.test)
        keyword = "} else if" if is_elif else "if"
        self.line(keyword + " (" + cond + ") {")
        self.indent_level += 1
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.indent_level -= 1

        if not node.orelse:
            self.line("}")
            return

        # If the orelse is a single If node, that's an elif — recurse.
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            self._emit_if_chain(node.orelse[0], True)
            return

        self.line("} else {")
        self.indent_level += 1
        for stmt in node.orelse:
            self.emit_stmt(stmt)
        self.indent_level -= 1
        self.line("}")

    def stmt_While(self, node):
        cond = self.emit_expr(node.test)
        self.line("while (" + cond + ") {")
        self.indent_level += 1
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.indent_level -= 1
        self.line("}")

    def stmt_Pass(self, node):
        # Haxe is fine with empty blocks; emit nothing.
        pass

    def stmt_Break(self, node):
        self.line("break;")

    def stmt_Continue(self, node):
        self.line("continue;")

    # === Expressions ===

    def expr_Constant(self, node):
        v = node.value
        if isinstance(v, bool):
            return "true" if v else "false"
        if v is None:
            return "null"
        if isinstance(v, str):
            escaped = v.replace("\\", "\\\\").replace('"', '\\"')
            return '"' + escaped + '"'
        return repr(v)

    def expr_Name(self, node):
        # Disciplined Python uses self for methods; map to Haxe this.
        if node.id == "self":
            return "this"
        return node.id

    def expr_BinOp(self, node):
        left = self.emit_expr(node.left)
        right = self.emit_expr(node.right)
        op = BINOP_MAP.get(type(node.op))
        if op is None:
            return "/* TODO binop: " + type(node.op).__name__ + " */"
        return "(" + left + " " + op + " " + right + ")"

    def expr_UnaryOp(self, node):
        operand = self.emit_expr(node.operand)
        op = UNARYOP_MAP.get(type(node.op))
        if op is None:
            return "/* TODO unaryop */"
        return op + operand

    def expr_BoolOp(self, node):
        op = BOOLOP_MAP[type(node.op)]
        parts = [self.emit_expr(v) for v in node.values]
        return "(" + (" " + op + " ").join(parts) + ")"

    def expr_Compare(self, node):
        # Disciplined Python doesn't use comparison chaining (a < b < c).
        left = self.emit_expr(node.left)
        op = COMPARE_MAP[type(node.ops[0])]
        right = self.emit_expr(node.comparators[0])
        return left + " " + op + " " + right

    def expr_Call(self, node):
        func = self.emit_expr(node.func)
        args = [self.emit_expr(a) for a in node.args]
        # Kwargs are handled by Milestone 5; for now mark them.
        for kw in node.keywords:
            args.append("/*kwarg " + kw.arg + "=*/" + self.emit_expr(kw.value))
        return func + "(" + ", ".join(args) + ")"

    def expr_Attribute(self, node):
        return self.emit_expr(node.value) + "." + node.attr


# ============================================================
# CLI
# ============================================================

def convert(source, filename="<input>"):
    tree = ast.parse(source, filename=filename)
    emitter = HaxeEmitter()
    emitter.emit_module(tree)
    return emitter.output()


def main():
    if len(sys.argv) != 2:
        print("usage: haxe_emitter.py FILE.py", file=sys.stderr)
        return 1
    f = open(sys.argv[1], "r")
    try:
        source = f.read()
    finally:
        f.close()
    print(convert(source, sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
