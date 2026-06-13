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

    Milestone 3: collections and iteration. for-loops over collections
    (`for x in items`) and ranges (`for i in range(N)`), list literals
    (`[a, b, c]`), dict literals (`{"a": 1}` -> `["a" => 1]`),
    subscripting (read and write), len(x) -> x.length, list.append ->
    list.push.

    Milestone 4: kwargs handling. Functions with default values emit as
    options-struct (typedef + single-param function with destructuring
    prelude); functions without defaults stay positional and call sites
    with kwargs are reordered to match parameter declaration order.

    Milestone 5: exceptions. try/except/raise mapped to Haxe try/catch/throw,
    with `Exception` mapping to `haxe.Exception`. Multiple catch handlers
    chain correctly. Re-raise (`raise e`) translates to `throw e`. The
    discipline checker flags try/finally, try/else, and bare `raise`
    (none of which have clean Haxe equivalents).

    Milestone 6: type system extensions and tuples. PEP 604 union syntax
    (`X | None`, `X | Y`), `Union[A, B, C]`, `Optional[X]`, `Callable`,
    `Any`, and `object` all map cleanly. Python tuples (`tuple[A, B]`,
    `(a, b)` literals) are translated using auto-generated TupleN classes
    emitted at module scope only for arities that appear in the source.
    Tuple-typed variables get `t[0]` rewritten to `t._0` for typed access,
    `t[i]` to `t.at(i)` for runtime indexing.

    Milestone 7: modules and the Main wrapper. Free functions are hoisted
    into a generated `Main` class as static methods. Calls to them from
    inside Main use the bare name; calls from other classes get prefixed
    with `Main.`. The `if __name__ == "__main__":` idiom is detected and
    its body becomes `Main.main()`. typing/discipline imports are silently
    dropped; other imports emit a placeholder comment until cross-file
    analysis is added.

    Milestone 8: polish. Precedence-aware parens (only emitted when
    structurally required, so `a + b * c` stays paren-free). Trailing
    blank lines before closing braces are post-processed away. Comments
    extracted via tokenize and preserved in the output as `// ...` lines
    (placement is line-based — comments inside function bodies appear in
    the right spot; module-level comments may shift slightly when free
    functions get hoisted into Main).

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
    # Python's built-in Exception maps to Haxe's recommended base class.
    # Common builtin exception subclasses have no Haxe equivalent, so they
    # collapse to haxe.Exception too: `raise ValueError(msg)` becomes
    # `throw new haxe.Exception(msg)` and `except ValueError` catches it.
    "Exception": "haxe.Exception",
    "ValueError": "haxe.Exception",
    "TypeError": "haxe.Exception",
    "KeyError": "haxe.Exception",
    "IndexError": "haxe.Exception",
    "RuntimeError": "haxe.Exception",
    "ArithmeticError": "haxe.Exception",
    "ZeroDivisionError": "haxe.Exception",
    "NotImplementedError": "haxe.Exception",
    # Escape-hatch types — Python's Any and object both translate to
    # Haxe's Dynamic (anything goes, type-checking suppressed).
    "Any": "Dynamic",
    "object": "Dynamic",
}

GENERIC_TYPES = {
    "List": "Array",
    "Optional": "Null",
    "Dict": "Map",
    # Union[A, B] -> haxe.extern.EitherType<A, B>. Multi-arg unions
    # nest left-to-right: Union[A, B, C] -> EitherType<A, EitherType<B, C>>.
    "Union": "haxe.extern.EitherType",
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
    # `is` and `is not` are identity checks in Python; for the common
    # case of comparing against None they map cleanly to Haxe's == / !=.
    # Disciplined Python avoids `is` for non-None comparisons.
    ast.Is: "==",
    ast.IsNot: "!=",
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

# Operator precedence — higher number binds tighter. Used to decide
# when binary operations need parens. Values mirror standard precedence
# in Python and Haxe (which agree on these). Anything not in the table
# is treated as 0 (lowest), forcing parens.
OPERATOR_PRECEDENCE = {
    # Boolean operators (lowest binding).
    ast.Or: 1,
    ast.And: 2,
    ast.Not: 3,  # Unary not
    # Comparison operators.
    ast.Eq: 4,
    ast.NotEq: 4,
    ast.Lt: 4,
    ast.LtE: 4,
    ast.Gt: 4,
    ast.GtE: 4,
    ast.Is: 4,
    ast.IsNot: 4,
    # Bitwise (also used for PEP 604 union types in annotations).
    ast.BitOr: 5,
    ast.BitAnd: 7,
    # Arithmetic.
    ast.Add: 10,
    ast.Sub: 10,
    ast.Mult: 11,
    ast.Div: 11,
    ast.Mod: 11,
    # Unary minus / plus bind tighter than multiplication.
    ast.USub: 12,
    ast.UAdd: 12,
}

AUGASSIGN_MAP = {
    ast.Add: "+=",
    ast.Sub: "-=",
    ast.Mult: "*=",
    ast.Div: "/=",
    ast.Mod: "%=",
}

# Python list/dict method names that have direct Haxe equivalents under
# different names. Applied at call sites where the call is shaped like
# obj.method(args).
METHOD_RENAMES = {
    "append": "push",
}

# Python string methods that map to Haxe StringTools static functions.
# Applied type-directed: `s.method(args)` -> `StringTools.fn(s, args)`
# only when the receiver is a known string. Haxe's String has its own
# toLowerCase/toUpperCase/indexOf/split, so those stay as method calls;
# only the ones living on StringTools are rewritten here.
STRINGTOOLS_METHODS = {
    "strip": "trim",
    "lstrip": "ltrim",
    "rstrip": "rtrim",
    "startswith": "startsWith",
    "endswith": "endsWith",
    "replace": "replace",
}

# Python string methods that map to a same-shape Haxe String method
# under a different name (receiver stays the receiver).
STRING_METHOD_RENAMES = {
    "lower": "toLowerCase",
    "upper": "toUpperCase",
    "find": "indexOf",
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
        # Class registry: { class_name: { "bases": [...], "methods": set(...),
        # "method_signatures": {name: signature} } }. Built by a first pass
        # over the module so that emission can detect method overrides and
        # emit the `override` keyword Haxe requires.
        self.classes = {}
        # Function registry: { function_name: signature }. Built alongside
        # classes; used for kwarg resolution at module-level call sites.
        self.functions = {}
        # Tuple arities seen during emission. Each unique arity gets one
        # generated TupleN class emitted at the top of the output.
        # Tuples are heterogeneous fixed-length records; see development
        # notes for the design rationale.
        self.tuple_arities = set()
        # Variable types tracked from annotated assignments and parameter
        # declarations. Used to rewrite `t[0]` -> `t._0` for tuple-typed
        # locals. Maps name -> (kind, arity) where kind is "tuple" or
        # other future kinds. Module-scope only for now.
        self.var_types = {}
        # Names already declared (with `var`) in the current function
        # scope. Haxe — unlike Python — requires `var` on a local's first
        # assignment but forbids redeclaration. Disciplined Python often
        # annotates the first bind (an AnnAssign), but plain `name = value`
        # first-binds also occur; stmt_Assign emits `var` the first time a
        # name is seen and a bare assignment thereafter. Saved/restored at
        # each function boundary alongside var_types.
        self.declared_vars = set()
        # Names of free functions that get hoisted into the generated
        # Main class. Calls to these from outside Main need to be
        # qualified as Main.name; calls from inside Main can use the
        # bare name.
        self.main_functions = set()
        # Module-level constants/vars hoisted into Main as static fields.
        # References from outside Main are qualified `Main.name`.
        self.module_constants = set()
        self._module_consts = []
        # Comments extracted from the source via tokenize, sorted by
        # (line, column). The emitter drains comments whose line is
        # less than the next AST node's line, emitting them as `// ...`
        # before the node. Set externally before emit_module is called.
        self._comments = []
        self._comment_idx = 0
        # Highest line number we've emitted from. Tracks where we are
        # in the source so we can decide which comments are "behind us"
        # and should be emitted now.
        self._emit_line = 0
        # Stack of enclosing-operator precedences. emit_expr is called
        # from many contexts; each operator pushes its own precedence
        # before recursing into operands, so child operators can decide
        # whether to wrap themselves in parens. Top of stack is the
        # immediate parent's precedence; 0 means no operator context
        # (statement-level expression — never needs outer parens).
        self._prec_stack = [0]

    def line(self, text):
        self.lines.append("    " * self.indent_level + text)

    def output(self):
        # Strip trailing blank lines that appear immediately before a
        # closing brace. These accumulate inside class bodies because
        # each method emits a blank line after its `}` for spacing
        # between methods, but the last method then leaves a hanging
        # blank before the class's closing `}`. Cosmetic only.
        cleaned = self._strip_blanks_before_close(self.lines)
        return "\n".join(cleaned)

    def _strip_blanks_before_close(self, lines):
        # Walk the list and skip any blank line whose next non-blank line
        # is a closing brace (`}` possibly indented).
        result = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.strip() == "":
                # Look ahead: if the next non-blank line closes a block,
                # drop this blank.
                j = i + 1
                while j < len(lines) and lines[j].strip() == "":
                    j += 1
                if j < len(lines) and lines[j].strip() == "}":
                    i += 1
                    continue
            result.append(line)
            i += 1
        return result

    # === Dispatch ===

    def emit_stmt(self, node):
        # Drain any source comments that appear before this statement.
        line = getattr(node, "lineno", None)
        if line is not None:
            self._drain_comments_before(line)
            self._emit_line = line
        method = "stmt_" + type(node).__name__
        handler = getattr(self, method, None)
        if handler is None:
            self.line("// TODO stmt: " + type(node).__name__)
            return
        handler(node)

    def _drain_comments_before(self, line):
        # Emit all stored comments whose line number is < the given line.
        # Comments on column 0 are standalone; comments at column > 0
        # are inline (trailing) — for now both kinds get emitted as
        # standalone `// ...` lines at the current indent.
        while self._comment_idx < len(self._comments):
            c_line, c_col, c_text = self._comments[self._comment_idx]
            if c_line >= line:
                return
            # Strip the leading '#' and any single space.
            body = c_text.lstrip("#").lstrip(" ")
            self.line("// " + body)
            self._comment_idx += 1

    def _drain_remaining_comments(self):
        # Emit any leftover comments after the last statement.
        while self._comment_idx < len(self._comments):
            _, _, c_text = self._comments[self._comment_idx]
            body = c_text.lstrip("#").lstrip(" ")
            self.line("// " + body)
            self._comment_idx += 1

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
            return self._emit_subscript_type(node)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            # PEP 604 syntax: `X | None`, `X | Y`, etc.
            return self._emit_union_binop(node)
        if isinstance(node, ast.Constant):
            # String forward-references like "Counter" or None for Void.
            if node.value is None:
                return "Void"
            if isinstance(node.value, str):
                return PYTHON_TO_HAXE_TYPES.get(node.value, node.value)
        return "/* TODO type */"

    def _emit_subscript_type(self, node):
        base = node.value.id if isinstance(node.value, ast.Name) else "?"
        slice_node = node.slice
        # Python <3.9 wrapped the slice in ast.Index; handle both for safety
        if hasattr(ast, "Index") and isinstance(slice_node, ast.Index):
            slice_node = slice_node.value

        # tuple[A, B] / Tuple[A, B] — record arity and emit Tuple<N>.
        if base in ("tuple", "Tuple"):
            return self._emit_tuple_type(slice_node)

        # Callable[[A, B], R] -> (A, B) -> R
        if base == "Callable":
            return self._emit_callable_type(slice_node)

        # Union[A, B] / Union[A, B, C] -> EitherType nested binary.
        if base == "Union":
            elts = slice_node.elts if isinstance(slice_node, ast.Tuple) else [slice_node]
            return self._emit_union_chain(elts)

        haxe_base = GENERIC_TYPES.get(base, base)
        # Tuple slice -> multi-arg generic, e.g. Dict[K, V] -> Map<K, V>
        if isinstance(slice_node, ast.Tuple):
            inner_parts = [self.emit_type(elt) for elt in slice_node.elts]
            return haxe_base + "<" + ", ".join(inner_parts) + ">"
        inner = self.emit_type(slice_node)
        return haxe_base + "<" + inner + ">"

    def _emit_tuple_type(self, slice_node):
        # tuple[A, B, C] -> Tuple3<A, B, C>. Records the arity so the
        # corresponding TupleN class gets emitted at module scope.
        if isinstance(slice_node, ast.Tuple):
            elts = slice_node.elts
        else:
            elts = [slice_node]
        arity = len(elts)
        self.tuple_arities.add(arity)
        inner_parts = [self.emit_type(elt) for elt in elts]
        return "Tuple" + str(arity) + "<" + ", ".join(inner_parts) + ">"

    def _emit_callable_type(self, slice_node):
        # Callable[[A, B], R]. The slice is a Tuple of (List of args, R).
        if not isinstance(slice_node, ast.Tuple) or len(slice_node.elts) != 2:
            return "/* TODO callable */"
        args_node, return_node = slice_node.elts
        if isinstance(args_node, ast.List):
            arg_types = [self.emit_type(a) for a in args_node.elts]
        else:
            arg_types = []
        ret_type = self.emit_type(return_node)
        if not arg_types:
            return "() -> " + ret_type
        return "(" + ", ".join(arg_types) + ") -> " + ret_type

    def _emit_union_binop(self, node):
        # Flatten left-leaning chains: A | B | C is BinOp(BinOp(A, B), C).
        elts = self._flatten_union_binop(node)
        return self._emit_union_chain(elts)

    def _flatten_union_binop(self, node):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._flatten_union_binop(node.left) + self._flatten_union_binop(node.right)
        return [node]

    def _emit_union_chain(self, elts):
        # `X | None` is the Optional pattern -> Null<X>.
        none_indices = [i for i, e in enumerate(elts) if self._is_none_constant(e)]
        if len(elts) == 2 and none_indices:
            other = elts[1 - none_indices[0]]
            return "Null<" + self.emit_type(other) + ">"
        # General union: nest right-associative as EitherType<A, EitherType<B, C>>.
        return self._nest_either(elts)

    def _nest_either(self, elts):
        if len(elts) == 1:
            return self.emit_type(elts[0])
        if len(elts) == 2:
            return ("haxe.extern.EitherType<" +
                    self.emit_type(elts[0]) + ", " +
                    self.emit_type(elts[1]) + ">")
        return ("haxe.extern.EitherType<" +
                self.emit_type(elts[0]) + ", " +
                self._nest_either(elts[1:]) + ">")

    def _is_none_constant(self, node):
        return isinstance(node, ast.Constant) and node.value is None

    # === Module ===

    def emit_module(self, node):
        # First pass: build the class and function registries so emission
        # can detect method overrides, resolve kwargs to positional, and
        # decide which functions take options structs.
        self._scan_classes(node)
        self._scan_functions(node)

        # Partition module body into:
        #   decls            — classes and imports (stay at top level)
        #   free_functions   — module-level def's (hoisted into Main)
        #   main_body        — statements that should run on startup
        # Haxe doesn't allow free statements or free functions at module
        # scope, so the latter two get folded into a generated Main class.
        decls, free_functions, main_body, module_consts = self._partition_module_body(node.body)

        # Track which functions are now Main static methods, so calls
        # to them from inside Main can use the bare name.
        self.main_functions = {f.name for f in free_functions}
        # Module-level constants/variables become static fields on Main.
        # References to them from inside Main use the bare name; from other
        # classes they are qualified `Main.name` (mirrors main_functions).
        self.module_constants = set()
        for stmt in module_consts:
            name = self._module_const_name(stmt)
            self.module_constants.add(name)
            kind = self._type_kind_of(getattr(stmt, "annotation", None))
            if kind is None:
                kind = self._infer_value_kind(getattr(stmt, "value", None))
            if kind is not None:
                self.var_types[name] = kind
        self._module_consts = module_consts

        # Emit declarations into a separate list so we can prepend any
        # auto-generated TupleN classes once we know which arities were
        # actually used during emission.
        body_lines = []
        saved = self.lines
        self.lines = body_lines
        for stmt in decls:
            self.emit_stmt(stmt)
        self.lines = saved
        self._emit_tuple_classes()
        self.lines.extend(body_lines)

        # Generate Main class if it has any content (free functions,
        # module-level constants, or main-body statements).
        if free_functions or main_body or module_consts:
            self._emit_main_class(free_functions, main_body, module_consts)

    def _partition_module_body(self, body):
        decls = []
        free_functions = []
        main_body = []
        module_consts = []
        for stmt in body:
            if self._is_module_docstring(stmt):
                # Module docstrings are dropped entirely (same as Python
                # would: they're available as __doc__ but don't execute).
                continue
            if isinstance(stmt, ast.FunctionDef):
                # Free functions need a home in Haxe (no module-scope
                # functions). We hoist them into Main as static methods.
                free_functions.append(stmt)
            elif isinstance(stmt, (ast.ClassDef, ast.Import, ast.ImportFrom)):
                decls.append(stmt)
            elif self._is_module_constant(stmt):
                # Module-level `NAME: T = value` (or `NAME = value`) — a
                # constant/global. Haxe has no module scope, so these become
                # static fields on Main.
                module_consts.append(stmt)
            elif self._is_main_guard(stmt):
                # Unwrap the body of `if __name__ == "__main__":` —
                # those statements run on startup in Python, so they
                # become Main.main() body.
                main_body.extend(stmt.body)
            else:
                # Other free statements at module scope (rare in
                # disciplined code, but possible) also go into main().
                main_body.append(stmt)
        return decls, free_functions, main_body, module_consts

    def _module_const_name(self, stmt):
        if isinstance(stmt, ast.AnnAssign):
            return stmt.target.id
        return stmt.targets[0].id

    def _is_module_constant(self, stmt):
        # A module-level assignment to a bare Name: `NAME: T = value`
        # (AnnAssign) or `NAME = value` (Assign, single Name target).
        if isinstance(stmt, ast.AnnAssign):
            return isinstance(stmt.target, ast.Name)
        if isinstance(stmt, ast.Assign):
            return len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
        return False

    def _is_module_docstring(self, stmt):
        return (isinstance(stmt, ast.Expr) and
                isinstance(stmt.value, ast.Constant) and
                isinstance(stmt.value.value, str))

    def _is_declaration(self, stmt):
        # Declarations stay at top level. Imports are kept here too;
        # stmt_Import / stmt_ImportFrom decide what to do with them.
        return isinstance(stmt, (ast.ClassDef, ast.FunctionDef,
                                 ast.Import, ast.ImportFrom))

    def _is_main_guard(self, stmt):
        # Detect `if __name__ == "__main__":` exactly. Anything else
        # (including `if __name__ != "__main__":`) is just a regular if
        # and stays out of this special handling.
        if not isinstance(stmt, ast.If):
            return False
        test = stmt.test
        if not isinstance(test, ast.Compare):
            return False
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            return False
        left = test.left
        right = test.comparators[0]
        is_dunder_name = (isinstance(left, ast.Name) and left.id == "__name__")
        is_main_str = (isinstance(right, ast.Constant) and right.value == "__main__")
        return is_dunder_name and is_main_str

    def _emit_main_class(self, free_functions, main_body, module_consts=None):
        self.line("class Main {")
        self.indent_level += 1
        prev_in_class = self.in_class
        prev_class_name = self.current_class_name
        self.in_class = True
        self.current_class_name = "Main"
        # Module-level constants become public static fields, emitted first
        # so methods and other classes can reference them as Main.NAME.
        if module_consts:
            for stmt in module_consts:
                self._emit_module_constant(stmt)
            self.line("")
        # Emit each free function as a static method.
        for func in free_functions:
            self._emit_static_function_in_class(func)
        # Then the main() entry point.
        if main_body:
            self.line("public static function main():Void {")
            self.indent_level += 1
            # Body emits in non-class context.
            saved_in_class = self.in_class
            self.in_class = False
            for stmt in main_body:
                self.emit_stmt(stmt)
            self.in_class = saved_in_class
            self.indent_level -= 1
            self.line("}")
        else:
            # No main() body — still emit a stub so Haxe has an entry.
            self.line("public static function main():Void {}")
        self.in_class = prev_in_class
        self.current_class_name = prev_class_name
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _emit_module_constant(self, stmt):
        # Emit a module-level constant/global as a public static field on
        # Main. Annotated form keeps its type; bare assignment lets Haxe
        # infer from the initializer.
        if isinstance(stmt, ast.AnnAssign):
            name = stmt.target.id
            type_str = self.emit_type(stmt.annotation)
            if stmt.value is None:
                self.line("public static var " + name + ":" + type_str + ";")
            else:
                value = self.emit_expr(stmt.value)
                self.line("public static var " + name + ":" + type_str + " = " + value + ";")
            return
        # Plain Assign: NAME = value.
        name = stmt.targets[0].id
        value = self.emit_expr(stmt.value)
        self.line("public static var " + name + " = " + value + ";")

    def _emit_static_function_in_class(self, node):
        # Emit a free function as a `public static function` inside the
        # Main class. Reuses the regular module-function logic but with
        # the static prefix and visibility forced.
        signature = self.functions.get(node.name)
        if signature is not None and signature["uses_options"]:
            # Options-struct version. The typedef has already been
            # emitted at the appropriate level (or will be — let's keep
            # the options form working).
            type_name = self._options_typename(node.name)
            self._emit_options_typedef(signature, type_name)
            ret = self.emit_type(node.returns)
            self.line("public static function " + node.name +
                      "(options:" + type_name + "):" + ret + " {")
            self.indent_level += 1
            # Body of the function emits in non-class context — locals
            # inside should be plain var, not public var.
            prev_in_class = self.in_class
            self.in_class = False
            prev_var_types = dict(self.var_types)
            prev_declared = self.declared_vars
            self.declared_vars = set()
            self._emit_options_prelude(signature)
            for stmt in node.body:
                self.emit_stmt(stmt)
            self.in_class = prev_in_class
            self.var_types = prev_var_types
            self.declared_vars = prev_declared
            self.indent_level -= 1
            self.line("}")
            self.line("")
            return

        params = self._format_params(node.args)
        ret = self.emit_type(node.returns)
        self.line("public static function " + node.name +
                  "(" + params + "):" + ret + " {")
        self.indent_level += 1
        prev_in_class = self.in_class
        self.in_class = False
        prev_var_types = dict(self.var_types)
        prev_declared = self.declared_vars
        self.declared_vars = set()
        self._register_param_var_types(node.args)
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.in_class = prev_in_class
        self.var_types = prev_var_types
        self.declared_vars = prev_declared
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _emit_tuple_classes(self):
        # One generated TupleN class per arity used. Generic over its
        # element types so a single class covers all type combinations.
        # See development notes for the design rationale.
        for arity in sorted(self.tuple_arities):
            self._emit_one_tuple_class(arity)

    def _emit_one_tuple_class(self, arity):
        type_params = ", ".join("T" + str(i) for i in range(arity))
        ctor_params = ", ".join("_" + str(i) + ":T" + str(i) for i in range(arity))
        self.line("class Tuple" + str(arity) + "<" + type_params + "> {")
        self.indent_level += 1
        # Public fields _0, _1, ...
        for i in range(arity):
            self.line("public var _" + str(i) + ":T" + str(i) + ";")
        # Lazy-initialized array for indexed access and iteration.
        self.line("private var _items:Array<Dynamic>;")
        # Constructor.
        self.line("public function new(" + ctor_params + "):Void {")
        self.indent_level += 1
        for i in range(arity):
            self.line("this._" + str(i) + " = _" + str(i) + ";")
        self.indent_level -= 1
        self.line("}")
        # at(i) — runtime indexing, returns Dynamic.
        self.line("public function at(i:Int):Dynamic {")
        self.indent_level += 1
        self.line("if (this._items == null) this._items = " +
                  "[" + ", ".join("this._" + str(i) for i in range(arity)) + "];")
        self.line("return this._items[i];")
        self.indent_level -= 1
        self.line("}")
        # iterator() — Iterable<Dynamic> conformance.
        self.line("public function iterator():Iterator<Dynamic> {")
        self.indent_level += 1
        self.line("if (this._items == null) this._items = " +
                  "[" + ", ".join("this._" + str(i) for i in range(arity)) + "];")
        self.line("return this._items.iterator();")
        self.indent_level -= 1
        self.line("}")
        # equals(other) — structural equality.
        self.line("public function equals(other:Tuple" + str(arity) +
                  "<" + type_params + ">):Bool {")
        self.indent_level += 1
        comparisons = " && ".join("this._" + str(i) + " == other._" + str(i)
                                  for i in range(arity))
        self.line("return " + comparisons + ";")
        self.indent_level -= 1
        self.line("}")
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _scan_classes(self, module_node):
        for stmt in module_node.body:
            if not isinstance(stmt, ast.ClassDef):
                continue
            bases = []
            for base in stmt.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
            methods = set()
            method_signatures = {}
            for item in stmt.body:
                if isinstance(item, ast.FunctionDef):
                    methods.add(item.name)
                    method_signatures[item.name] = self._build_signature(item)
            self.classes[stmt.name] = {
                "bases": bases,
                "methods": methods,
                "method_signatures": method_signatures,
            }

    def _scan_functions(self, module_node):
        # Module-level function signatures, used for kwarg resolution at
        # call sites that target free functions.
        if not hasattr(self, "functions"):
            self.functions = {}
        for stmt in module_node.body:
            if isinstance(stmt, ast.FunctionDef):
                self.functions[stmt.name] = self._build_signature(stmt)

    def _build_signature(self, func_node):
        # Capture parameter names in order, paired with their default
        # nodes (or None). Defaults are right-aligned in Python's AST,
        # so the last N args have the last N defaults.
        non_self = [a for a in func_node.args.args if a.arg not in ("self", "cls")]
        defaults = func_node.args.defaults
        first_default_index = len(non_self) - len(defaults)

        params = []  # list of {name, annotation, default}
        for i, arg in enumerate(non_self):
            default_node = None
            if i >= first_default_index:
                default_node = defaults[i - first_default_index]
            params.append({
                "name": arg.arg,
                "annotation": arg.annotation,
                "default": default_node,
            })

        has_defaults = any(p["default"] is not None for p in params)
        return {"params": params, "uses_options": has_defaults,
                "returns": func_node.returns}

    def _resolve_to_positional(self, signature, call_node):
        # Given a signature and a call's positional + keyword args, return
        # a list of expression strings in parameter order. Positional args
        # fill from the left; remaining slots are matched by keyword name;
        # any still-unfilled slot uses the parameter's default expression.
        params = signature["params"]
        result = []
        kwargs_by_name = {kw.arg: kw.value for kw in call_node.keywords}

        for i, param in enumerate(params):
            if i < len(call_node.args):
                # Positional arg supplied for this slot.
                result.append(self.emit_expr(call_node.args[i]))
            elif param["name"] in kwargs_by_name:
                result.append(self.emit_expr(kwargs_by_name[param["name"]]))
            elif param["default"] is not None:
                result.append(self.emit_expr(param["default"]))
            else:
                # No positional, no kwarg, no default — call is invalid.
                # The discipline checker should have caught this; emit
                # a TODO so it surfaces in the output.
                result.append("/* TODO missing arg: " + param["name"] + " */")
        return result

    def _format_options_literal(self, signature, call_node):
        # Build a Haxe object literal { name: value, ... } from the call's
        # positional and keyword arguments, mapped against the signature's
        # parameter names. Defaults are NOT inlined — the function's
        # destructuring prelude applies them when the field is null.
        params = signature["params"]
        kwargs_by_name = {kw.arg: kw.value for kw in call_node.keywords}
        pieces = []

        for i, param in enumerate(params):
            value = None
            if i < len(call_node.args):
                value = self.emit_expr(call_node.args[i])
            elif param["name"] in kwargs_by_name:
                value = self.emit_expr(kwargs_by_name[param["name"]])
            # If neither positional nor kwarg was supplied, omit the field
            # entirely — the prelude will use the default.
            if value is not None:
                pieces.append(param["name"] + ": " + value)
        return "{ " + ", ".join(pieces) + " }"

    def _lookup_signature(self, call_node):
        # Determine which signature, if any, matches this call.
        # Returns (signature, kind) where kind is "function", "constructor",
        # "method", or None if not resolvable.
        func = call_node.func
        # ClassName(...) -> constructor
        if isinstance(func, ast.Name) and self._looks_like_class_call(func):
            cls = self.classes.get(func.id)
            if cls is not None:
                init = cls["method_signatures"].get("__init__")
                if init is not None:
                    return (init, "constructor", func.id)
        # foo(...) -> module-level function
        if isinstance(func, ast.Name):
            sig = self.functions.get(func.id)
            if sig is not None:
                return (sig, "function", func.id)
        # self.method(...) -> method on current class
        if isinstance(func, ast.Attribute) \
                and isinstance(func.value, ast.Name) \
                and func.value.id == "self" \
                and self.current_class_name is not None:
            cls = self.classes.get(self.current_class_name)
            if cls is not None:
                sig = cls["method_signatures"].get(func.attr)
                if sig is not None:
                    return (sig, "method", func.attr)
        return (None, None, None)

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

    # Modules whose imports are tooling-only and should be silently
    # dropped during emission. typing imports vanish (the type machinery
    # is consumed during emission, not imported in Haxe). The discipline
    # module provides @haxe_extern, also tooling-only. __future__ is
    # Python-version compatibility, not relevant.
    SILENT_IMPORT_MODULES = {"typing", "__future__", "discipline", "pyhaxe.discipline"}

    def stmt_ImportFrom(self, node):
        # Drop typing/tooling imports silently.
        if node.module in self.SILENT_IMPORT_MODULES:
            return
        # Other imports: emit a comment marking the spot. Cross-module
        # analysis (resolving imported classes, generating proper Haxe
        # `import` statements) is deferred — for now the user sees a
        # placeholder showing what was imported.
        names = ", ".join(self._format_alias(a) for a in node.names)
        self.line("// import: from " + str(node.module) + " import " + names)

    def stmt_Import(self, node):
        names = ", ".join(self._format_alias(a) for a in node.names)
        self.line("// import: import " + names)

    def _format_alias(self, alias):
        if alias.asname:
            return alias.name + " as " + alias.asname
        return alias.name

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
        signature = self.functions.get(node.name)
        if signature is not None and signature["uses_options"]:
            self._emit_options_function(node, signature, type_name=self._options_typename(node.name))
            return

        params = self._format_params(node.args)
        ret = self.emit_type(node.returns)
        self.line("function " + node.name + "(" + params + "):" + ret + " {")
        self.indent_level += 1
        prev_var_types = dict(self.var_types)
        prev_declared = self.declared_vars
        self.declared_vars = set()
        self._register_param_var_types(node.args)
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.var_types = prev_var_types
        self.declared_vars = prev_declared
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _register_param_var_types(self, args):
        # Record each parameter's type kind in var_types so subscript,
        # slice, and membership operators can branch on str vs Array vs
        # Map vs tuple inside the function body.
        for arg in args.args:
            if arg.arg in ("self", "cls"):
                continue
            # Parameters are already in scope — record them as declared so
            # a reassignment in the body doesn't get a spurious `var`.
            self.declared_vars.add(arg.arg)
            kind = self._type_kind_of(arg.annotation)
            if kind is not None:
                self.var_types[arg.arg] = kind

    def _type_kind_of(self, type_node):
        # Map a type annotation to a tracked var-type kind tuple. Returns
        # one of ("tuple", arity), ("str",), ("array",), ("map",) or None
        # when the kind isn't one we branch on. Used to drive type-directed
        # subscript/slice/membership emission.
        if type_node is None:
            return None
        arity = self._tuple_arity_of(type_node)
        if arity is not None:
            return ("tuple", arity)
        # Bare `str` annotation (Name or forward-ref string constant).
        if isinstance(type_node, ast.Name):
            if type_node.id == "str":
                return ("str",)
            return None
        if isinstance(type_node, ast.Constant) and isinstance(type_node.value, str):
            if type_node.value == "str":
                return ("str",)
            return None
        # Generic subscripts: List[...] -> array, Dict[...] -> map.
        if isinstance(type_node, ast.Subscript):
            base = type_node.value.id if isinstance(type_node.value, ast.Name) else None
            if base in ("List", "list"):
                return ("array",)
            if base in ("Dict", "dict"):
                return ("map",)
        return None

    def _infer_value_kind(self, value_node):
        # Best-effort kind inference for the right-hand side of an
        # untyped assignment (`ch = text[i]`, `tokens = []`, ...). Only
        # confident cases return a kind; everything else returns None so
        # we don't mistrack. Used by stmt_Assign.
        if value_node is None:
            return None
        # String literal -> str.
        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
            return ("str",)
        # List literal -> array.
        if isinstance(value_node, ast.List):
            return ("array",)
        # Dict literal -> map.
        if isinstance(value_node, ast.Dict):
            return ("map",)
        # `text[i]` where text is a known str yields a str (charAt result).
        if isinstance(value_node, ast.Subscript) and isinstance(value_node.value, ast.Name):
            base_kind = self.var_types.get(value_node.value.id)
            if base_kind is not None and base_kind[0] == "str":
                slice_node = value_node.slice
                if hasattr(ast, "Index") and isinstance(slice_node, ast.Index):
                    slice_node = slice_node.value
                # A slice of a str is still a str; an index of a str is a
                # single-char str (Haxe charAt returns String).
                return ("str",)
        # Call to a known free function -> kind of its declared return type
        # (e.g. `tokens = tokenize(text)` where tokenize -> List[Token]).
        if isinstance(value_node, ast.Call) and isinstance(value_node.func, ast.Name):
            sig = self.functions.get(value_node.func.id)
            if sig is not None:
                return self._type_kind_of(sig.get("returns"))
        # String-op call result (s.strip(), s.charAt(...), ...).
        sk = self._static_kind(value_node)
        if sk is not None:
            return sk
        return None

    def _options_typename(self, function_name, class_name=None):
        # FooOptions for module-level Foo. ClassNewOptions for __init__.
        # ClassFooOptions for Class.foo().
        if function_name == "__init__":
            return class_name + "NewOptions"
        if class_name is None:
            return self._capitalize(function_name) + "Options"
        return class_name + self._capitalize(function_name) + "Options"

    def _capitalize(self, name):
        if not name:
            return name
        return name[0].upper() + name[1:]

    def _emit_options_typedef(self, signature, type_name):
        # Emit a Haxe typedef from the parameters. Required params (no
        # default) become required fields; defaulted params become
        # optional (`?` prefix). Underscore prefixes on parameter names
        # are not stripped here — parameters aren't visibility-bearing
        # in the same way fields and methods are.
        self.line("typedef " + type_name + " = {")
        self.indent_level += 1
        last = len(signature["params"]) - 1
        for i, param in enumerate(signature["params"]):
            optional = "?" if param["default"] is not None else ""
            line = optional + param["name"] + ":" + self.emit_type(param["annotation"])
            if i < last:
                line += ","
            self.line(line)
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _emit_options_prelude(self, signature):
        # For each parameter, emit a local `var name:Type = options.name`
        # (or with default fallback for optional params). After the
        # prelude, the rest of the body uses these locals just as if
        # they were ordinary parameters.
        for param in signature["params"]:
            type_str = self.emit_type(param["annotation"])
            name = param["name"]
            # The prelude binds each parameter as a local `var`; record it
            # as declared and track its kind for type-directed emission.
            self.declared_vars.add(name)
            kind = self._type_kind_of(param["annotation"])
            if kind is not None:
                self.var_types[name] = kind
            if param["default"] is None:
                self.line("var " + name + ":" + type_str + " = options." + name + ";")
            else:
                default_expr = self.emit_expr(param["default"])
                self.line("var " + name + ":" + type_str +
                          " = (options." + name + " != null) ? options." + name +
                          " : " + default_expr + ";")

    def _emit_options_function(self, node, signature, type_name):
        # Top-level function variant: emit typedef, then function with a
        # single options param, then the prelude, then the original body.
        self._emit_options_typedef(signature, type_name)
        ret = self.emit_type(node.returns)
        self.line("function " + node.name + "(options:" + type_name + "):" + ret + " {")
        self.indent_level += 1
        prev_var_types = dict(self.var_types)
        prev_declared = self.declared_vars
        self.declared_vars = set()
        self._emit_options_prelude(signature)
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.var_types = prev_var_types
        self.declared_vars = prev_declared
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
                base_name = PYTHON_TO_HAXE_TYPES.get(base.id, base.id)
                extends_clause = " extends " + base_name

        # Emit typedefs for any methods that use options, before the
        # class itself. Haxe typedefs live at module scope.
        cls_info = self.classes.get(node.name)
        if cls_info is not None:
            for method_name, signature in cls_info["method_signatures"].items():
                if signature["uses_options"]:
                    typedef_name = self._options_typename(method_name, class_name=node.name)
                    self._emit_options_typedef(signature, typedef_name)

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
        is_private = (not is_init) and self._is_private_name(node.name)
        if is_init:
            name = "new"
        else:
            name = self._strip_private_underscores(node.name)
        ret = "Void" if is_init else self.emit_type(node.returns)
        params = self._format_params(node.args)

        visibility = "private" if is_private else "public"
        if is_static:
            prefix = visibility + " static function "
        else:
            prefix = visibility + " function "
        self.line(prefix + name + "(" + params + "):" + ret + ";")

    def _emit_method(self, node):
        is_static = self._has_decorator(node, "staticmethod")
        is_init = node.name == "__init__"
        is_private = (not is_init) and self._is_private_name(node.name)
        # __init__ has no return type annotation — Haxe constructors are Void.
        # The emitted method name strips leading underscores (Haxe doesn't
        # use the convention) and `private` carries the visibility.
        if is_init:
            name = "new"
        else:
            name = self._strip_private_underscores(node.name)
        ret = "Void" if is_init else self.emit_type(node.returns)

        # Look up the method's signature to decide between options-struct
        # and positional form.
        signature = None
        cls_info = self.classes.get(self.current_class_name)
        if cls_info is not None:
            signature = cls_info["method_signatures"].get(node.name)
        uses_options = signature is not None and signature["uses_options"]

        if uses_options:
            type_name = self._options_typename(node.name, class_name=self.current_class_name)
            params = "options:" + type_name
        else:
            params = self._format_params(node.args)

        # Constructors and static methods can't be overrides; for the rest,
        # check the class registry for the same method name in any ancestor.
        is_override = (not is_static) and (not is_init) and \
            self._is_override(self.current_class_name, node.name)

        # Visibility modifier: explicit private for underscore-prefixed
        # names, public otherwise. The discipline treats _foo and __foo
        # the same way; both emit as `private`.
        visibility = "private" if is_private else "public"

        if is_static:
            prefix = visibility + " static function "
        elif is_override:
            prefix = "override " + visibility + " function "
        else:
            prefix = visibility + " function "

        self.line(prefix + name + "(" + params + "):" + ret + " {")
        self.indent_level += 1
        # Method body emits in non-class context — locals inside the body
        # are regular var declarations, not field declarations.
        prev_in_class = self.in_class
        self.in_class = False
        prev_var_types = dict(self.var_types)
        prev_declared = self.declared_vars
        self.declared_vars = set()
        if not uses_options:
            self._register_param_var_types(node.args)
        # For options methods, emit the destructuring prelude first so
        # the rest of the body can use the parameter names as locals.
        if uses_options:
            self._emit_options_prelude(signature)
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.in_class = prev_in_class
        self.var_types = prev_var_types
        self.declared_vars = prev_declared
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

    def _is_private_name(self, name):
        # Python convention: leading underscore signals private. The
        # discipline treats `_foo` and `__foo` the same way; double
        # underscore in Python triggers name mangling but for our
        # purposes both are just "not public".
        # Dunders like __init__ are special and handled separately by
        # the caller (they map to `new` and aren't private).
        return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))

    def _strip_private_underscores(self, name):
        # `__foo` -> `foo`, `_foo` -> `foo`. Haxe doesn't use leading
        # underscores for visibility, so we drop them and let the
        # `private` modifier carry the meaning.
        return name.lstrip("_")

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
        # Track the target's type kind (tuple / str / array / map) so
        # expr_Subscript, slice handling, and membership tests can branch.
        if isinstance(node.target, ast.Name):
            kind = self._type_kind_of(node.annotation)
            if kind is not None:
                self.var_types[node.target.id] = kind
            # Record the declaration so a later plain reassignment of the
            # same name emits a bare `name = ...` rather than a second `var`.
            if not self.in_class:
                self.declared_vars.add(node.target.id)
        # Inside a class body, this is a field declaration. Apply
        # visibility based on the leading-underscore convention; outside
        # a class, it's just a top-level var.
        if self.in_class:
            # Detect the raw field name on the AnnAssign target so we
            # can check for the underscore prefix and strip it.
            if isinstance(node.target, ast.Name):
                raw_name = node.target.id
                if self._is_private_name(raw_name):
                    target = self._strip_private_underscores(raw_name)
                    visibility = "private"
                else:
                    visibility = "public"
            else:
                visibility = "public"
            prefix = visibility + " var "
        else:
            prefix = "var "
        if node.value is None:
            self.line(prefix + target + ":" + type_str + ";")
            return
        value = self.emit_expr(node.value)
        self.line(prefix + target + ":" + type_str + " = " + value + ";")

    def stmt_Assign(self, node):
        # Disciplined Python single-target only.
        # Track an inferred type kind for plain `name = value` assignments
        # so subsequent subscript/slice/membership on the name can branch
        # (e.g. `ch = text[i]` makes ch a str). Only confident inferences
        # are recorded; ambiguous RHS leaves the name untracked.
        target_node = node.targets[0]
        is_new_local = False
        if isinstance(target_node, ast.Name):
            name = target_node.id
            if name not in self.var_types:
                kind = self._infer_value_kind(node.value)
                if kind is not None:
                    self.var_types[name] = kind
            # A bare Name assignment outside a class body that hasn't been
            # declared yet is a new local; Haxe needs `var`. Inside a class
            # body, bare Names are field-ish and handled elsewhere, so only
            # apply this in function/method scope (self.in_class is False
            # while emitting bodies).
            if not self.in_class and name not in self.declared_vars:
                is_new_local = True
                self.declared_vars.add(name)
        target = self.emit_expr(target_node)
        value = self.emit_expr(node.value)
        prefix = "var " if is_new_local else ""
        self.line(prefix + target + " = " + value + ";")

    def stmt_AugAssign(self, node):
        target = self.emit_expr(node.target)
        value = self.emit_expr(node.value)
        op = AUGASSIGN_MAP.get(type(node.op))
        if op is None:
            self.line("// TODO augassign: " + type(node.op).__name__)
            return
        self.line(target + " " + op + " " + value + ";")

    def _emit_block_body(self, body, extra_declared=None):
        # Emit a nested block's statements under their own declared-var
        # scope. Haxe `var` declarations are block-scoped: a name first
        # bound inside this block must not leak to sibling blocks (so each
        # gets its own `var`), but names from the enclosing scope are
        # visible. We snapshot declared_vars on entry and restore on exit;
        # `extra_declared` seeds names the block header introduces (e.g. a
        # for-loop target, which Haxe declares implicitly).
        saved = set(self.declared_vars)
        if extra_declared:
            self.declared_vars |= extra_declared
        for stmt in body:
            self.emit_stmt(stmt)
        self.declared_vars = saved

    def stmt_If(self, node):
        self._emit_if_chain(node, False)

    def _emit_if_chain(self, node, is_elif):
        cond = self.emit_expr(node.test)
        keyword = "} else if" if is_elif else "if"
        self.line(keyword + " (" + cond + ") {")
        self.indent_level += 1
        self._emit_block_body(node.body)
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
        self._emit_block_body(node.orelse)
        self.indent_level -= 1
        self.line("}")

    def stmt_While(self, node):
        cond = self.emit_expr(node.test)
        self.line("while (" + cond + ") {")
        self.indent_level += 1
        self._emit_block_body(node.body)
        self.indent_level -= 1
        self.line("}")

    def stmt_For(self, node):
        # Disciplined Python: target is always a single Name (no tuple
        # unpacking), iter is either a `range(...)` call or a collection.
        target = self.emit_expr(node.target)
        iter_expr = self._format_for_iter(node.iter)
        self.line("for (" + target + " in " + iter_expr + ") {")
        self.indent_level += 1
        # The loop variable is declared by the `for` header in Haxe, so
        # reassignments to it inside the body must not re-emit `var`.
        extra = set()
        if isinstance(node.target, ast.Name):
            extra.add(node.target.id)
        self._emit_block_body(node.body, extra_declared=extra)
        self.indent_level -= 1
        self.line("}")

    def _format_for_iter(self, node):
        # `range(N)` -> `0...N`,  `range(start, stop)` -> `start...stop`.
        # Haxe's `...` is exclusive on the right, matching Python's range.
        # Three-argument range with a step is not supported in Haxe's
        # range literal; emit a TODO so it surfaces in output.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "range":
            args = node.args
            if len(args) == 1:
                return "0..." + self.emit_expr(args[0])
            if len(args) == 2:
                return self.emit_expr(args[0]) + "..." + self.emit_expr(args[1])
            return "/* TODO range with step */"
        return self.emit_expr(node)

    def stmt_Pass(self, node):
        # Haxe is fine with empty blocks; emit nothing.
        pass

    def stmt_Break(self, node):
        self.line("break;")

    def stmt_Continue(self, node):
        self.line("continue;")

    def stmt_Try(self, node):
        # Python try/except/else/finally -> Haxe try/catch.
        # Haxe doesn't support `finally` or the `else` clause, so we
        # detect those and emit a TODO comment. The discipline checker
        # also flags them.
        self.line("try {")
        self.indent_level += 1
        self._emit_block_body(node.body)
        self.indent_level -= 1

        # Each handler opens with `} catch (...)` (closing the previous
        # block, opening its own); the very last one closes with `}`.
        for handler in node.handlers:
            self._emit_except_handler_open(handler)
            self.indent_level += 1
            # The caught name (`catch (e:...)`) is declared by the header.
            extra = {handler.name} if handler.name else set()
            self._emit_block_body(handler.body, extra_declared=extra)
            self.indent_level -= 1
        if node.handlers:
            self.line("}")
        else:
            self.line("}")

        if node.orelse:
            self.line("// TODO: try/else has no Haxe equivalent")
        if node.finalbody:
            self.line("// TODO: try/finally has no Haxe equivalent")

    def _emit_except_handler_open(self, handler):
        # Emit only the `} catch (...) {` line; the body and closing brace
        # are managed by the caller.
        if handler.type is None:
            name = handler.name if handler.name else "_"
            self.line("} catch (" + name + ") {")
        else:
            type_str = self.emit_type(handler.type)
            name = handler.name if handler.name else "_"
            self.line("} catch (" + name + ":" + type_str + ") {")

    def stmt_Raise(self, node):
        # `raise` (bare) — Python re-raises the current exception. The
        # discipline requires the explicit form `raise e`, so a bare
        # raise here means the lint missed something.
        if node.exc is None:
            self.line("/* TODO: bare raise has no Haxe equivalent; use `raise e` */")
            return
        # `raise SomeException("msg")` -> `throw new SomeException("msg")`.
        # The expr_Call class-instantiation logic already adds `new` for
        # capitalized names, so this works without special handling.
        # `raise e` (re-raise a caught name) -> `throw e`. Also works.
        self.line("throw " + self.emit_expr(node.exc) + ";")

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
        # Module-level constants live as static fields on Main. References
        # from other classes must be qualified `Main.NAME`; references from
        # inside Main use the bare name (sibling static field).
        if node.id in self.module_constants and self.current_class_name != "Main":
            return "Main." + node.id
        # Names like `Exception` need to map to their Haxe equivalents
        # when used as constructor calls (e.g., `raise Exception(...)`).
        # The same map drives type-position translation.
        return PYTHON_TO_HAXE_TYPES.get(node.id, node.id)

    def expr_BinOp(self, node):
        # printf-style string formatting (`"%g" % value`) has no operator
        # form in Haxe. We map the common single-substitution numeric/string
        # specifiers to Std.string of the operand — adequate for compact
        # value rendering (the only use in the target code). A literal
        # format with no conversion just passes through.
        if isinstance(node.op, ast.Mod) and self._is_format_string(node.left):
            return self._emit_string_format(node.left, node.right)
        op = BINOP_MAP.get(type(node.op))
        if op is None:
            return "/* TODO binop: " + type(node.op).__name__ + " */"
        my_prec = OPERATOR_PRECEDENCE.get(type(node.op), 0)
        parent_prec = self._prec_stack[-1]
        self._prec_stack.append(my_prec)
        left = self.emit_expr(node.left)
        right = self.emit_expr(node.right)
        self._prec_stack.pop()
        result = left + " " + op + " " + right
        if parent_prec > my_prec:
            return "(" + result + ")"
        return result

    def _is_format_string(self, node):
        return (isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "%" in node.value)

    def _emit_string_format(self, fmt_node, args_node):
        # Translate `"...%spec..." % args` into Haxe string concatenation,
        # substituting Std.string(arg) for each conversion. `args` may be a
        # single value or a tuple of values. Width/precision/flags in the
        # spec are dropped (Haxe has no printf in the standard library);
        # this is sufficient for compact value rendering and is the
        # disciplined fallback. A genuinely format-dependent case should be
        # reworked on the Python side.
        fmt = fmt_node.value
        if isinstance(args_node, ast.Tuple):
            args = list(args_node.elts)
        else:
            args = [args_node]
        pieces = []
        literal = ""
        arg_i = 0
        i = 0
        n = len(fmt)
        while i < n:
            ch = fmt[i]
            if ch == "%" and i + 1 < n:
                nxt = fmt[i + 1]
                if nxt == "%":
                    literal += "%"
                    i += 2
                    continue
                # Consume a (simplified) conversion specifier up to the
                # type char, ignoring flags/width/precision.
                j = i + 1
                while j < n and fmt[j] not in "diouxXeEfFgGsrc":
                    j += 1
                if j < n and arg_i < len(args):
                    if literal:
                        pieces.append(self._haxe_str_literal(literal))
                        literal = ""
                    pieces.append("Std.string(" + self.emit_expr(args[arg_i]) + ")")
                    arg_i += 1
                    i = j + 1
                    continue
            literal += ch
            i += 1
        if literal:
            pieces.append(self._haxe_str_literal(literal))
        if not pieces:
            return self._haxe_str_literal("")
        return " + ".join(pieces)

    def _haxe_str_literal(self, s):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return '"' + escaped + '"'

    def _static_kind(self, node):
        # Best-effort kind of an expression node (str / array / map),
        # used for type-directed truthiness and membership. Returns a kind
        # tuple or None. Recognizes tracked names, literals, and the
        # results of the string/collection operations we emit.
        if isinstance(node, ast.Name):
            return self.var_types.get(node.id)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return ("str",)
        if isinstance(node, ast.List):
            return ("array",)
        if isinstance(node, ast.Dict):
            return ("map",)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            # `s.strip()` -> str; `.charAt`/`.substring` -> str. These are
            # the string ops the emitter produces.
            if node.func.attr in ("strip", "lstrip", "rstrip", "lower",
                                   "upper", "charAt", "substring", "substr"):
                return ("str",)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            base = self.var_types.get(node.value.id)
            if base is not None and base[0] == "str":
                return ("str",)
        return None

    def expr_UnaryOp(self, node):
        # Type-directed truthiness for `not x`. Python `not s` / `not arr`
        # is true when the value is None or empty; Haxe `!s` is a type error
        # on String/Array. Map to an explicit null-or-empty test.
        if isinstance(node.op, ast.Not):
            kind = self._static_kind(node.operand)
            if kind is not None and kind[0] in ("str", "array"):
                inner = self.emit_expr(node.operand)
                return "(" + inner + " == null || " + inner + ".length == 0)"
            if kind is not None and kind[0] == "map":
                inner = self.emit_expr(node.operand)
                return "(" + inner + " == null)"
        op = UNARYOP_MAP.get(type(node.op))
        if op is None:
            return "/* TODO unaryop */"
        my_prec = OPERATOR_PRECEDENCE.get(type(node.op), 0)
        parent_prec = self._prec_stack[-1]
        self._prec_stack.append(my_prec)
        operand = self.emit_expr(node.operand)
        self._prec_stack.pop()
        result = op + operand
        if parent_prec > my_prec:
            return "(" + result + ")"
        return result

    def expr_BoolOp(self, node):
        op = BOOLOP_MAP[type(node.op)]
        my_prec = OPERATOR_PRECEDENCE.get(type(node.op), 0)
        parent_prec = self._prec_stack[-1]
        self._prec_stack.append(my_prec)
        parts = [self.emit_expr(v) for v in node.values]
        self._prec_stack.pop()
        result = (" " + op + " ").join(parts)
        if parent_prec > my_prec:
            return "(" + result + ")"
        return result

    def expr_Compare(self, node):
        # Disciplined Python doesn't use comparison chaining (a < b < c).
        my_prec = OPERATOR_PRECEDENCE.get(type(node.ops[0]), 0)
        parent_prec = self._prec_stack[-1]
        self._prec_stack.append(my_prec)
        left = self.emit_expr(node.left)
        op = COMPARE_MAP[type(node.ops[0])]
        right = self.emit_expr(node.comparators[0])
        self._prec_stack.pop()
        result = left + " " + op + " " + right
        if parent_prec > my_prec:
            return "(" + result + ")"
        return result

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
            return self._format_super_init_call(node)

        # Special case: len(x) -> x.length. Python builtin -> Haxe property.
        if isinstance(node.func, ast.Name) and node.func.id == "len" \
                and len(node.args) == 1:
            return self.emit_expr(node.args[0]) + ".length"

        # Special case: str(x) -> Std.string(x). Python builtin -> Haxe
        # standard library function. Only the single-argument form maps
        # cleanly; str() with no args (returns "") and str(x, encoding)
        # don't have direct Haxe equivalents.
        if isinstance(node.func, ast.Name) and node.func.id == "str" \
                and len(node.args) == 1:
            return "Std.string(" + self.emit_expr(node.args[0]) + ")"

        # Numeric parses/casts: float(x) -> Std.parseFloat(Std.string(x))
        # for strings, but disciplined code uses float(str). Std.parseFloat
        # takes a String; int(x) -> Std.parseInt (also String-typed) or
        # Std.int for float-to-int truncation. We map the common
        # string-parse forms used by tokenizers.
        if isinstance(node.func, ast.Name) and node.func.id == "float" \
                and len(node.args) == 1:
            return "Std.parseFloat(" + self.emit_expr(node.args[0]) + ")"
        if isinstance(node.func, ast.Name) and node.func.id == "int" \
                and len(node.args) == 1:
            # int(s) where s is a known str -> parse; otherwise truncate.
            arg = node.args[0]
            if isinstance(arg, ast.Name) and self.var_types.get(arg.id) == ("str",):
                return "Std.parseInt(" + self.emit_expr(arg) + ")"
            return "Std.int(" + self.emit_expr(arg) + ")"

        # Type-directed string methods on a known-string receiver.
        if isinstance(node.func, ast.Attribute):
            recv_kind = self._static_kind(node.func.value)
            if recv_kind == ("str",):
                attr = node.func.attr
                if attr in STRINGTOOLS_METHODS:
                    # StringTools.fn(receiver, args...)
                    receiver = self.emit_expr(node.func.value)
                    fn = STRINGTOOLS_METHODS[attr]
                    args = [self.emit_expr(a) for a in node.args]
                    all_args = [receiver] + args
                    return "StringTools." + fn + "(" + ", ".join(all_args) + ")"
                if attr in STRING_METHOD_RENAMES:
                    receiver = self.emit_expr(node.func.value)
                    args = [self.emit_expr(a) for a in node.args]
                    return (receiver + "." + STRING_METHOD_RENAMES[attr] +
                            "(" + ", ".join(args) + ")")

        # Method renames: list.append(x) -> list.push(x), etc. Applied
        # when the call is shaped like obj.method(args) and the method
        # name appears in METHOD_RENAMES.
        if isinstance(node.func, ast.Attribute) \
                and node.func.attr in METHOD_RENAMES:
            receiver = self.emit_expr(node.func.value)
            new_method = METHOD_RENAMES[node.func.attr]
            args = [self.emit_expr(a) for a in node.args]
            return receiver + "." + new_method + "(" + ", ".join(args) + ")"

        # Signature-aware path: if we can resolve the called function in
        # the registry, decide between options-struct form (function uses
        # defaults) and positional form (no defaults).
        signature, kind, _name = self._lookup_signature(node)
        if signature is not None:
            func = self._format_call_func(node.func, kind)
            if signature["uses_options"]:
                literal = self._format_options_literal(signature, node)
                if kind == "constructor":
                    return "new " + func + "(" + literal + ")"
                return func + "(" + literal + ")"
            else:
                resolved_args = self._resolve_to_positional(signature, node)
                if kind == "constructor":
                    return "new " + func + "(" + ", ".join(resolved_args) + ")"
                return func + "(" + ", ".join(resolved_args) + ")"

        # Unresolved fallback: emit positional args, mark any kwargs.
        # External library calls land here, as do calls on local variables
        # whose type we can't determine without type tracking.
        func = self._format_call_func(node.func, None)
        args = [self.emit_expr(a) for a in node.args]
        for kw in node.keywords:
            args.append("/*kwarg " + kw.arg + "=*/" + self.emit_expr(kw.value))

        if self._looks_like_class_call(node.func):
            return "new " + func + "(" + ", ".join(args) + ")"
        return func + "(" + ", ".join(args) + ")"

    def _format_call_func(self, func_node, kind):
        # When a free function is hoisted into the Main class, calls to
        # it from outside Main need a `Main.` prefix. Calls from inside
        # Main use the bare name (sibling static methods).
        if isinstance(func_node, ast.Name) and func_node.id in self.main_functions:
            if self.current_class_name != "Main":
                return "Main." + func_node.id
            return func_node.id
        return self.emit_expr(func_node)

    def _format_super_init_call(self, node):
        # Look up the parent class's __init__ signature so we can resolve
        # kwargs properly and pick options-struct vs positional form.
        parent_sig = None
        if self.current_class_name is not None:
            cls = self.classes.get(self.current_class_name)
            if cls is not None and cls["bases"]:
                parent_name = cls["bases"][0]
                parent = self.classes.get(parent_name)
                if parent is not None:
                    parent_sig = parent["method_signatures"].get("__init__")

        if parent_sig is not None:
            if parent_sig["uses_options"]:
                literal = self._format_options_literal(parent_sig, node)
                return "super(" + literal + ")"
            resolved = self._resolve_to_positional(parent_sig, node)
            return "super(" + ", ".join(resolved) + ")"

        # Fallback for unresolvable parent (extern, missing scan, etc.)
        args = [self.emit_expr(a) for a in node.args]
        return "super(" + ", ".join(args) + ")"

    def _looks_like_class_call(self, func_node):
        # Match a bare Name where the identifier is capitalized: Counter()
        if isinstance(func_node, ast.Name):
            name = func_node.id
            return len(name) > 0 and name[0].isupper()
        # Don't treat Foo.bar() as construction even if Foo is a class —
        # that's a static method call, which is just `Foo.bar()` in Haxe.
        return False

    def expr_Attribute(self, node):
        receiver = self.emit_expr(node.value)
        attr = node.attr
        # Private methods/fields use leading-underscore in disciplined
        # Python; the emitter strips the underscores and relies on the
        # `private` visibility modifier on the declaration. Apply this
        # only for self-references — stripping underscores from arbitrary
        # external attributes would corrupt names from third-party APIs.
        # Dunder attrs like __init__ are special and excluded.
        if receiver == "this" and self._is_private_name(attr):
            attr = self._strip_private_underscores(attr)
        return receiver + "." + attr

    def expr_List(self, node):
        elements = [self.emit_expr(e) for e in node.elts]
        return "[" + ", ".join(elements) + "]"

    def expr_Tuple(self, node):
        # Tuple literal in expression position -> new TupleN(...).
        # Records the arity so the corresponding TupleN class is emitted.
        arity = len(node.elts)
        self.tuple_arities.add(arity)
        elements = [self.emit_expr(e) for e in node.elts]
        return "new Tuple" + str(arity) + "(" + ", ".join(elements) + ")"

    def expr_Dict(self, node):
        # Haxe map literal syntax: ["key" => value, ...]. Empty dicts
        # are ambiguous (Haxe needs a type hint to disambiguate from an
        # empty array); emit `new Map()` as a safer default.
        if not node.keys:
            return "new Map()"
        pairs = []
        for key, value in zip(node.keys, node.values):
            pairs.append(self.emit_expr(key) + " => " + self.emit_expr(value))
        return "[" + ", ".join(pairs) + "]"

    def expr_Subscript(self, node):
        # Used for both reading (x = arr[i]) and writing (arr[i] = x).
        # Same surface syntax in Haxe; the AST parent (Assign vs not)
        # determines which.
        slice_node = node.slice
        # Python <3.9 wrapped the slice in ast.Index; handle both.
        if hasattr(ast, "Index") and isinstance(slice_node, ast.Index):
            slice_node = slice_node.value

        # Type-directed dispatch when we know the receiver's kind.
        var_info = None
        if isinstance(node.value, ast.Name):
            var_info = self.var_types.get(node.value.id)
        kind = var_info[0] if var_info is not None else None

        # Slice node (`x[a:b]`). Haxe has no slice syntax: a str slice maps
        # to String.substring(a, b) and an array slice to Array.slice(a, b).
        if isinstance(slice_node, ast.Slice):
            return self._emit_slice(node.value, slice_node, kind)

        # Tuple indexing: `t[0]` -> `t._0` when t is tuple-typed and the
        # index is a literal int (the common case, fully typed). For
        # variable indices on tuples, fall back to `t.at(i)` which
        # returns Dynamic.
        if kind == "tuple":
            receiver = self.emit_expr(node.value)
            if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, int):
                return receiver + "._" + str(slice_node.value)
            return receiver + ".at(" + self.emit_expr(slice_node) + ")"

        receiver = self.emit_expr(node.value)
        index = self.emit_expr(slice_node)
        # String element read: Haxe String has no array-read access, so
        # `text[i]` becomes `text.charAt(i)`. Arrays and maps keep `[...]`.
        if kind == "str":
            return receiver + ".charAt(" + index + ")"
        return receiver + "[" + index + "]"

    def _emit_slice(self, value_node, slice_node, kind):
        # Translate `x[a:b]` to a Haxe call. str -> substring, array ->
        # slice; both take (start, end) with end exclusive, matching
        # Python. A `step` is not expressible in either call form.
        if slice_node.step is not None:
            return "/* TODO slice step */"
        receiver = self.emit_expr(value_node)
        lower = self.emit_expr(slice_node.lower) if slice_node.lower is not None else "0"
        if kind == "str":
            method = "substring"
        elif kind == "array":
            method = "slice"
        else:
            # Unknown receiver kind: substring is the safe default for the
            # string-scanning code this targets; surface it as a TODO note
            # so a genuinely-untyped slice is still visible.
            method = "substring"
        if slice_node.upper is not None:
            upper = self.emit_expr(slice_node.upper)
            return receiver + "." + method + "(" + lower + ", " + upper + ")"
        return receiver + "." + method + "(" + lower + ")"

    def _tuple_arity_of(self, type_node):
        # Returns the arity if the annotation is a tuple type, else None.
        if isinstance(type_node, ast.Subscript):
            base = type_node.value.id if isinstance(type_node.value, ast.Name) else None
            if base in ("tuple", "Tuple"):
                slice_node = type_node.slice
                if hasattr(ast, "Index") and isinstance(slice_node, ast.Index):
                    slice_node = slice_node.value
                if isinstance(slice_node, ast.Tuple):
                    return len(slice_node.elts)
                return 1
        return None


# ============================================================
# CLI
# ============================================================

def convert(source, filename="<input>"):
    tree = ast.parse(source, filename=filename)
    emitter = HaxeEmitter()
    emitter._comments = _extract_comments(source)
    emitter.emit_module(tree)
    emitter._drain_remaining_comments()
    return emitter.output()


def _extract_comments(source):
    # Use tokenize to pull out (line, col, text) for every comment in
    # the source. ast.parse drops comments entirely, so this is the
    # only way to recover them. Returned sorted by (line, col).
    import tokenize
    import io
    comments = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                comments.append((tok.start[0], tok.start[1], tok.string))
    except tokenize.TokenizeError:
        # Tokenizer can fail on malformed source; we fall back to
        # an empty list rather than raising — the AST parse already
        # succeeded so emission can continue without comments.
        pass
    comments.sort(key=lambda c: (c[0], c[1]))
    return comments


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
