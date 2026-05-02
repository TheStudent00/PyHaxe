"""
haxe_emitter.py

Disciplined-Python -> Haxe converter.

Status:
    Milestone 1: top-level functions with typed parameters and return
    types, basic expressions (literals, names, binary ops, comparisons,
    boolean ops, unary ops, function calls, attribute access),
    annotated/regular/augmented assignments, if/elif/else, while loops,
    break, continue, pass.

    Milestone 2: classes with fields and methods, single inheritance,
    __init__ -> new, self -> this, @staticmethod, super() calls,
    parameter default values, @haxe_extern wrapper detection (emits
    `extern class` declarations).

Future milestones (in order):
    3. Collections and iteration — for x in coll, for i in range(N), len()
    4. Signature-aware kwargs resolution
    5. Try/except/raise
    6. Type system extensions — Optional, List, Dict -> Null, Array, Map
    7. Module/import system
    8. Polish — comments, formatting, error reporting

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
        # Context flags — let stmt_FunctionDef and stmt_AnnAssign know
        # whether they are emitting at module scope or inside a class body.
        self.in_class = False
        self.current_class_name = None
        # Class registry: { class_name: { "bases": [...], "methods": set(...) } }
        # Built by a first pass over the module so that emission can detect
        # method overrides and emit the `override` keyword Haxe requires.
        self.classes = {}

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
        if isinstance(node, ast.Constant):
            # String forward-references like "Counter" or None for Void.
            if node.value is None:
                return "Void"
            if isinstance(node.value, str):
                return PYTHON_TO_HAXE_TYPES.get(node.value, node.value)
        return "/* TODO type */"

    # === Module ===

    def emit_module(self, node):
        # First pass: build the class registry so the emitter can detect
        # method overrides during the second pass.
        self._scan_classes(node)
        for stmt in node.body:
            self.emit_stmt(stmt)

    def _scan_classes(self, module_node):
        for stmt in module_node.body:
            if not isinstance(stmt, ast.ClassDef):
                continue
            bases = []
            for base in stmt.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
            methods = set()
            for item in stmt.body:
                if isinstance(item, ast.FunctionDef):
                    methods.add(item.name)
            self.classes[stmt.name] = {"bases": bases, "methods": methods}

    def _is_override(self, class_name, method_name):
        # Walk up the inheritance chain looking for the method.
        if class_name is None:
            return False
        cls = self.classes.get(class_name)
        if cls is None:
            return False
        for base_name in cls["bases"]:
            base = self.classes.get(base_name)
            if base is None:
                continue
            if method_name in base["methods"]:
                return True
            # Recurse up the chain.
            if self._is_override(base_name, method_name):
                return True
        return False

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
        if self.in_class:
            self._emit_method(node)
            return
        # Module-level function.
        params = self._format_params(node.args)
        ret = self.emit_type(node.returns)
        self.line("function " + node.name + "(" + params + "):" + ret + " {")
        self.indent_level += 1
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def stmt_ClassDef(self, node):
        # Wrapper classes get an `extern class` stub — the body is
        # manually maintained per-target, not translated.
        if self._is_haxe_extern(node):
            self._emit_extern_class(node)
            return

        # Single inheritance only — the linter enforces this; here we
        # just take the first base if present.
        extends_clause = ""
        if node.bases:
            base = node.bases[0]
            if isinstance(base, ast.Name):
                extends_clause = " extends " + base.id

        self.line("class " + node.name + extends_clause + " {")
        self.indent_level += 1

        prev_in_class = self.in_class
        prev_class_name = self.current_class_name
        self.in_class = True
        self.current_class_name = node.name

        for stmt in node.body:
            # Skip docstrings inside class bodies.
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                    and isinstance(stmt.value.value, str):
                continue
            self.emit_stmt(stmt)

        self.in_class = prev_in_class
        self.current_class_name = prev_class_name

        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _emit_extern_class(self, node):
        # Find the @haxe_extern decorator and read its optional name argument.
        haxe_name = node.name
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) \
                    and dec.func.id == "haxe_extern":
                if dec.args and isinstance(dec.args[0], ast.Constant) \
                        and isinstance(dec.args[0].value, str):
                    haxe_name = dec.args[0].value
                break

        self.line("extern class " + haxe_name + " {")
        self.indent_level += 1

        # Emit method signatures only — no bodies. Field declarations
        # in the class body are kept (they describe the extern's shape).
        for stmt in node.body:
            if isinstance(stmt, ast.FunctionDef):
                self._emit_extern_method_signature(stmt)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                # Field on the extern.
                type_str = self.emit_type(stmt.annotation)
                self.line("var " + stmt.target.id + ":" + type_str + ";")
            # Skip docstrings and anything else.

        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _emit_extern_method_signature(self, node):
        is_static = self._has_decorator(node, "staticmethod")
        is_init = node.name == "__init__"
        name = "new" if is_init else node.name
        ret = "Void" if is_init else self.emit_type(node.returns)
        params = self._format_params(node.args)

        prefix = "static function " if is_static else "function "
        self.line(prefix + name + "(" + params + "):" + ret + ";")

    def _emit_method(self, node):
        is_static = self._has_decorator(node, "staticmethod")
        is_init = node.name == "__init__"
        name = "new" if is_init else node.name
        # __init__ has no return type annotation — Haxe constructors are Void.
        ret = "Void" if is_init else self.emit_type(node.returns)
        params = self._format_params(node.args)

        # Constructors and static methods can't be overrides; for the rest,
        # check the class registry for the same method name in any ancestor.
        is_override = (not is_static) and (not is_init) and \
            self._is_override(self.current_class_name, node.name)

        # Visibility modifier: public for everything by default; the
        # discipline doesn't currently model private.
        if is_static:
            prefix = "public static function "
        elif is_override:
            prefix = "override public function "
        else:
            prefix = "public function "

        self.line(prefix + name + "(" + params + "):" + ret + " {")
        self.indent_level += 1
        # Method body emits in non-class context — locals inside the body
        # are regular var declarations, not field declarations.
        prev_in_class = self.in_class
        self.in_class = False
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.in_class = prev_in_class
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _format_params(self, args):
        # Build the Haxe parameter list, skipping self/cls and applying
        # defaults where present. Python AST stores defaults right-aligned
        # to the parameter list (last N args have the last N defaults).
        params = []
        non_self_args = [a for a in args.args if a.arg not in ("self", "cls")]
        defaults = args.defaults
        first_default_index = len(non_self_args) - len(defaults)

        for i, arg in enumerate(non_self_args):
            piece = arg.arg + ":" + self.emit_type(arg.annotation)
            if i >= first_default_index:
                default_node = defaults[i - first_default_index]
                piece += " = " + self.emit_expr(default_node)
            params.append(piece)
        return ", ".join(params)

    def _has_decorator(self, node, name):
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == name:
                return True
        return False

    def _is_haxe_extern(self, node):
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) \
                    and dec.func.id == "haxe_extern":
                return True
            if isinstance(dec, ast.Name) and dec.id == "haxe_extern":
                return True
        return False

    def stmt_Return(self, node):
        if node.value is None:
            self.line("return;")
            return
        self.line("return " + self.emit_expr(node.value) + ";")

    def stmt_AnnAssign(self, node):
        target = self.emit_expr(node.target)
        type_str = self.emit_type(node.annotation)
        # Inside a class body, this is a field declaration — Haxe needs
        # `public var` for fields accessed from outside the class.
        prefix = "public var " if self.in_class else "var "
        if node.value is None:
            self.line(prefix + target + ":" + type_str + ";")
            return
        value = self.emit_expr(node.value)
        self.line(prefix + target + ":" + type_str + " = " + value + ";")

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
        # Special case: super().__init__(args) -> super(args).
        # In Python, super() is a function returning a proxy object, and
        # super().__init__(...) chains the parent constructor call.
        # In Haxe, super(...) is a direct keyword form inside `new`.
        if isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Call) \
                and isinstance(node.func.value.func, ast.Name) \
                and node.func.value.func.id == "super" \
                and node.func.attr == "__init__":
            args = [self.emit_expr(a) for a in node.args]
            return "super(" + ", ".join(args) + ")"

        func = self.emit_expr(node.func)
        args = [self.emit_expr(a) for a in node.args]
        # Kwargs are handled by Milestone 5; for now mark them.
        for kw in node.keywords:
            args.append("/*kwarg " + kw.arg + "=*/" + self.emit_expr(kw.value))

        # Class instantiation: in Python, ClassName(...) is a call; in Haxe
        # it must be written as `new ClassName(...)`. The disciplined-Python
        # convention is that classes use capitalized names, so we use that
        # as the heuristic. Module.Class also matches via the trailing name.
        if self._looks_like_class_call(node.func):
            return "new " + func + "(" + ", ".join(args) + ")"
        return func + "(" + ", ".join(args) + ")"

    def _looks_like_class_call(self, func_node):
        # Match a bare Name where the identifier is capitalized: Counter()
        if isinstance(func_node, ast.Name):
            name = func_node.id
            return len(name) > 0 and name[0].isupper()
        # Don't treat Foo.bar() as construction even if Foo is a class —
        # that's a static method call, which is just `Foo.bar()` in Haxe.
        return False

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
