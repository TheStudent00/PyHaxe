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

    Milestone 9: type-directed strings & collections. Variable type
    tracking (var_types) extended beyond tuples to record str / Array /
    Map / class kinds from annotations, confident RHS inference, and
    declared return types. Drives: str[i] -> charAt, str[a:b] ->
    substring, arr[a:b] -> slice; `in`/`not in` membership (indexOf /
    Lambda.has / Map.exists); type-directed truthiness (`not s` ->
    null-or-empty test); string methods (strip -> StringTools.trim, etc).
    Locals get `var` on first assignment with Haxe-correct block scoping.
    Module-level constants become Main static fields. Builtin exceptions
    (ValueError, ...) map to haxe.Exception. float()/int() parse helpers
    and `"%g" % v` formatting. Tagged-union subclass field access and
    returns route through casts (base.subfield -> (cast base).subfield).

    Cross-module imports (the roadmap's Milestone 8). The generated module
    class is named after the source file (`constraint_eval.py` ->
    `ConstraintEval`), not a hardcoded `Main`, so it doesn't collide when
    several modules compile together. `import x` / `from x import y` emit
    real Haxe `import X;` lines (hoisted above all declarations) and record
    the mapping so qualified refs `x.func(...)` resolve to `X.func(...)` and
    `from`-imported function/constant names resolve to `X.name`. Module
    classes still emit a `main()` so any of them can serve as a `-main`
    entry point. (The "Main wrapper" wording above is historical; the
    wrapper class is now per-module-named.)

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
    # Bare (un-parameterized) builtin collection annotations. With no
    # element type given, fall back to Dynamic element/value types.
    "list": "Array<Dynamic>",
    "dict": "Map<String, Dynamic>",
    # Bare `set` (no element type): Haxe Maps cannot key on Dynamic, so default
    # to String keys — matching the String-keyed bare `dict` default and the
    # disciplined code's string-id sets. A typed `Set[T]` keeps T.
    "set": "Map<String, Bool>",
    # Bare `tuple` annotation (no arity) — heterogeneous, so Dynamic. Typed
    # `Tuple[A, B]` annotations still resolve to a concrete TupleN elsewhere.
    "tuple": "Dynamic",
}

# Haxe top-level builtin functions that a Python identifier may collide with.
# A local var or nested function named e.g. `trace` would otherwise bind to
# Haxe's builtin (wrong type / `Void`), so such identifiers are renamed with a
# trailing underscore consistently at definition and use. Only genuine
# call-resolvable builtins are listed (not contextual keywords like `default`,
# which Haxe accepts as plain identifiers outside their construct).
HAXE_RESERVED_IDENTIFIERS = {
    "trace",
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
        # Nested (local) functions visible in the current body, mapping
        # name -> return-type kind tuple (or None). Module/class-level
        # functions live in self.functions; nested `def`s defined inside a
        # method body are otherwise invisible to type-directed logic, so a
        # call to one (e.g. the recursive `find_back_edge` in SnapDrag) can't
        # have its result coerced for boolean context. Saved/restored at each
        # function boundary like var_types.
        self.local_functions = {}
        # Pending `@<name>.setter` methods for the class currently being
        # emitted, mapping the property's raw Python name -> the setter
        # FunctionDef. Populated in stmt_ClassDef so the paired `@property`
        # getter emits a `(get, set)` property (F3).
        self._property_setters = {}
        # Kind tuple of the current function's declared return type, used to
        # insert a downcast when returning a base-typed value where a
        # subclass is declared (tagged-union pattern). Set per body entry.
        self._return_kind = None
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
        # Name of the generated module class that hosts free functions and
        # module constants. Derived from the source filename (Milestone 8:
        # `constraint_eval.py` -> `ConstraintEval`); defaults to "Main" when
        # no filename is available (REPL / single-snippet use), preserving
        # the historical single-file behaviour. `convert` sets this before
        # emit_module runs.
        self.module_class_name = "Main"
        # Cross-module imports (Milestone 8).
        #   imported_modules: { python_module_name: HaxeClassName } for
        #     `import x` — so a qualified ref `x.func(...)` resolves to
        #     `X.func(...)`.
        #   imported_names:   { local_name: HaxeQualifiedRef } for
        #     `from x import y [as z]` — so a bare `y`/`z` resolves to the
        #     static `X.y` on the imported module class.
        self.imported_modules = {}
        self.imported_names = {}
        # Local names bound to a non-portable builtin module (e.g. `random`),
        # whose member calls are rewritten inline rather than via a class.
        self.builtin_module_aliases = {}
        # Directory of the source file, used to locate sibling .py modules
        # for cross-module type-info scanning (so imported classes' field
        # and method types drive type-directed emission). None disables it
        # (REPL/string input).
        self._source_dir = None
        # Haxe `import X;` lines collected during the decls pass. Haxe
        # requires all imports to precede every type declaration, so they
        # are buffered here and flushed at the very top of the output
        # (before generated TupleN classes), not emitted inline.
        self._haxe_imports = []
        # Local names introduced by dropped backend imports (e.g. `Widget`,
        # `RelativeLayout` from `from kivy.uix... import ...`). If any of these
        # leak into emitted @haxe_extern signatures, a bare `extern class Name
        # {}` stub is emitted so the module type-checks standalone (the real
        # type is supplied by the per-target backend).
        self._backend_type_names = set()
        # class name -> set of raw (pre-strip) class-variable names emitted as
        # Haxe statics. `self.NAME` reads of these resolve to `ClassName.NAME`.
        self.class_statics = {}
        # (class name, raw static name) -> inferred kind tuple, for statics
        # whose kind comes from their value (no annotation), e.g. an
        # `Array<Tuple2<...>>` constant.
        self.class_static_kinds = {}
        # When True, generated TupleN helper classes are NOT inlined into each
        # module; instead an `import Tuples;` is emitted and the classes live in
        # a single shared `Tuples.hx` (see emit_tuples_module). This avoids
        # "Type Tuple2 is redefined" when several modules in one build dir each
        # use tuples. Default False preserves single-module/standalone output.
        self.shared_tuples = False
        # Set True the first time a dynamic/unknown-kind operand is lowered to
        # `Pyhaxe.truthy(...)` in boolean context (F8). Callers can read it to
        # know whether the shared `Pyhaxe.hx` runtime module must be emitted.
        self._uses_truthy = False
        # When a module's holder-class name collides with one of its own
        # classes, the module constants/free functions are folded into that
        # class as statics (set in emit_module).
        self._merge_into_class = None
        self._merge_consts = []
        self._merge_functions = []

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

        # Set[T] / set[T] -> Map<T, Bool>: Haxe has no Set, so a set is
        # modeled as a Map whose values are all true (membership = key
        # existence).
        if base in ("Set", "set"):
            inner = self.emit_type(slice_node)
            return "Map<" + inner + ", Bool>"

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
        # decide which functions take options structs. Imported sibling
        # modules are scanned first (for cross-module type info), then the
        # local module — so a local definition always wins on name clash.
        self._scan_imported_modules(node)
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

        # If the module's holder-class name (used for free functions /
        # module constants) collides with a class defined IN the module
        # (e.g. handles.py defines `class Handles` and the module is
        # `Handles`), Haxe would see two `Handles` types in one module. Fold
        # the module constants and free functions into that existing class as
        # statics instead of emitting a separate holder class.
        self._merge_into_class = None
        if self.module_class_name in self.classes \
                and (free_functions or module_consts):
            self._merge_into_class = self.module_class_name
            self._merge_consts = module_consts
            self._merge_functions = free_functions

        # Emit declarations into a separate list so we can prepend any
        # auto-generated TupleN classes once we know which arities were
        # actually used during emission.
        body_lines = []
        saved = self.lines
        self.lines = body_lines
        for stmt in decls:
            self.emit_stmt(stmt)
        self.lines = saved
        # Haxe requires every `import` to precede all type declarations, so
        # cross-module imports collected during the decls pass are flushed
        # first (Milestone 8), then the generated TupleN classes, then the
        # rest of the declarations.
        # In shared-tuples mode, pull the TupleN classes out of this module and
        # depend on the shared `Tuples` module instead (import flushed below).
        # A wildcard import brings every TupleN sub-type into scope by short
        # name so `new Tuple2(...)` / `Tuple2<...>` resolve unchanged.
        self._needs_tuples_import = self.shared_tuples and bool(self.tuple_arities)
        self._emit_haxe_imports()
        self._emit_backend_type_stubs(body_lines)
        if not self.shared_tuples:
            self._emit_tuple_classes()
        self.lines.extend(body_lines)

        # Generate Main class if it has any content (free functions,
        # module-level constants, or main-body statements). When the consts /
        # free functions were merged into a same-named class above, only a
        # remaining main-body (rare) still needs a holder — but that too would
        # collide, so it is dropped (disciplined library modules have no
        # __main__ body).
        if self._merge_into_class is not None:
            pass
        elif free_functions or main_body or module_consts:
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
            if self._is_type_checking_guard(stmt):
                # `if TYPE_CHECKING:` holds imports used only for forward type
                # references (e.g. cyclic imports). The type IS referenced in
                # emitted signatures, so its import must still be emitted —
                # route the guard's imports into decls. Non-import statements
                # in the guard are typing-only and dropped.
                for inner in stmt.body:
                    if isinstance(inner, (ast.Import, ast.ImportFrom)):
                        decls.append(inner)
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
        # Drop backend-glue free functions: a module-level def that is
        # referenced ONLY inside @haxe_extern class bodies is helper code for
        # the dropped backend (e.g. kit.py's `_parse_rgba`, called only by the
        # extern UI/widget bodies). It is not part of the disciplined surface,
        # so hoisting it into the module class would emit non-portable Haxe for
        # code that no portable caller reaches. Functions used anywhere outside
        # an extern body are kept.
        if free_functions:
            free_functions = self._drop_extern_only_functions(free_functions, body)
        return decls, free_functions, main_body, module_consts

    def _drop_extern_only_functions(self, free_functions, body):
        fn_names = {f.name for f in free_functions}
        # A free function is dropped only if it is referenced INSIDE an
        # @haxe_extern class body AND nowhere outside one. Functions never
        # referenced internally at all are kept — they are the module's public
        # API (called cross-module), e.g. ConstraintEval.evaluate. Only the
        # pure backend-glue helpers (used solely by dropped extern bodies, like
        # kit.py's `_parse_rgba`) are removed.
        used_in_extern = set()
        used_outside = set()
        for stmt in body:
            is_extern = isinstance(stmt, ast.ClassDef) and self._is_haxe_extern(stmt)
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Name) and sub.id in fn_names:
                    (used_in_extern if is_extern else used_outside).add(sub.id)
        drop = (used_in_extern - used_outside)
        return [f for f in free_functions if f.name not in drop]

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

    def _is_type_checking_guard(self, stmt):
        # Detect `if TYPE_CHECKING:` (the typing.TYPE_CHECKING constant, False
        # at runtime). Its body holds type-only imports / forward refs.
        if not isinstance(stmt, ast.If):
            return False
        return isinstance(stmt.test, ast.Name) and stmt.test.id == "TYPE_CHECKING"

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
        self.line("class " + self.module_class_name + " {")
        self.indent_level += 1
        prev_in_class = self.in_class
        prev_class_name = self.current_class_name
        self.in_class = True
        self.current_class_name = self.module_class_name
        # Module-level constants become public static fields, emitted first
        # so methods and other classes can reference them as Main.NAME.
        if module_consts:
            for stmt in module_consts:
                self._emit_module_constant(stmt)
            self.line("")
        # F2: a module-level `def main()` plus an `if __name__ == "__main__"`
        # guard would BOTH want to emit `static function main()`. In Haxe,
        # `-main Module` auto-invokes `Module.main()`, so a user `def main()`
        # already serves as the entry point and the guard body (which in
        # disciplined code just calls `main()`) is redundant. When a user
        # `main` function is present, emit it as the entry and suppress the
        # synthetic wrapper, unless the guard body does more than invoke
        # `main()` — in that case the synthetic wrapper still carries the
        # extra startup statements but skips the now-duplicate `main()` call.
        user_main = any(f.name == "main" for f in free_functions)
        if user_main and main_body:
            main_body = [s for s in main_body
                         if not self._is_bare_call_to(s, "main")]
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
        elif not user_main:
            # No main() body and no user-defined main — still emit a stub so
            # Haxe has an entry. (When a user `main` exists it IS the entry.)
            self.line("public static function main():Void {}")
        self.in_class = prev_in_class
        self.current_class_name = prev_class_name
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _is_bare_call_to(self, stmt, name):
        # True if `stmt` is `name()` (a no-arg call to the given function as a
        # statement). Used to strip the redundant `main()` from a
        # `if __name__ == "__main__":` guard body when a user `def main`
        # already provides the entry point (F2).
        return (isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
                and stmt.value.func.id == name
                and not stmt.value.args
                and not stmt.value.keywords)

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
            prev_local_funcs = self.local_functions
            self.local_functions = dict(prev_local_funcs)
            self.declared_vars = set()
            self._return_kind = self._type_kind_of(node.returns)
            self._emit_options_prelude(signature)
            self._register_local_functions(node.body)
            for stmt in node.body:
                self.emit_stmt(stmt)
            self.in_class = prev_in_class
            self.var_types = prev_var_types
            self.declared_vars = prev_declared
            self.local_functions = prev_local_funcs
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
        prev_local_funcs = self.local_functions
        self.local_functions = dict(prev_local_funcs)
        self.declared_vars = set()
        self._return_kind = self._type_kind_of(node.returns)
        self._register_param_var_types(node.args)
        self._register_local_functions(node.body)
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.in_class = prev_in_class
        self.var_types = prev_var_types
        self.declared_vars = prev_declared
        self.local_functions = prev_local_funcs
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _emit_haxe_imports(self):
        # Emit buffered cross-module `import X;` lines at the top of the
        # file (Haxe requires imports before any declaration). A module
        # import brings the module's main type and its sibling types into
        # scope by their short names.
        needs_tuples = getattr(self, "_needs_tuples_import", False)
        if not self._haxe_imports and not needs_tuples:
            return
        if needs_tuples:
            # Import each used TupleN type from the shared Tuples module by its
            # full path so it resolves by short name (Haxe needs the explicit
            # type path; a bare module import wouldn't bring multi-type modules
            # into scope).
            for arity in sorted(self.tuple_arities):
                self.line("import Tuples.Tuple" + str(arity) + ";")
        for module_class in self._haxe_imports:
            self.line("import " + module_class + ";")
        self.line("")

    def _emit_backend_type_stubs(self, body_lines):
        # Emit a bare `extern class Name {}` for each dropped-backend local
        # name that leaked into an emitted signature (field/param/return type).
        # These are platform/backend types (e.g. Kivy's Widget/RelativeLayout)
        # exposed by @haxe_extern signatures; the real definition lives in the
        # per-target backend. A bare extern lets the module type-check
        # standalone. Names defined as classes in this module are skipped.
        if not self._backend_type_names:
            return
        import re
        defined = set()
        for ln in body_lines:
            m = re.match(r"\s*(?:extern )?class (\w+)", ln)
            if m:
                defined.add(m.group(1))
        text = "\n".join(body_lines)
        stubs = []
        for name in sorted(self._backend_type_names):
            if name in defined:
                continue
            if re.search(r"\b" + re.escape(name) + r"\b", text):
                stubs.append(name)
        for name in stubs:
            self.line("extern class " + name + " {}")
        if stubs:
            self.line("")

    def _emit_tuple_classes(self):
        # One generated TupleN class per arity used. Generic over its
        # element types so a single class covers all type combinations.
        # See development notes for the design rationale.
        for arity in sorted(self.tuple_arities):
            self._emit_one_tuple_class(arity)

    def emit_tuples_module(self, arities):
        # Emit a standalone `Tuples.hx` defining every TupleN class for the
        # given arities — the shared home referenced by `import Tuples;` when
        # shared_tuples is on. Returns the source text.
        self.lines = []
        self.tuple_arities = set(arities)
        self._emit_tuple_classes()
        return self.output()

    def emit_runtime_module(self):
        # Emit a standalone `Pyhaxe.hx` carrying PyHaxe's runtime support
        # helpers — currently just `truthy`, the Python-semantic truthiness
        # used to lower a Dynamic/unknown operand in boolean context (F8).
        # Called fully-qualified as `Pyhaxe.truthy(x)`, so no per-module import
        # is needed: Haxe resolves the top-level type by filename. Ship it in
        # the build dir (like Tuples.hx) whenever any module uses truthy().
        self.lines = []
        self.line("// Auto-generated PyHaxe runtime support. Do not edit.")
        self.line("class Pyhaxe {")
        self.indent_level += 1
        self.line("// Python-semantic truthiness for a Dynamic value used in")
        self.line("// boolean context: false for null; for Bool the value; for")
        self.line("// Int/Float `!= 0`; for String non-empty; for Array/Map")
        self.line("// non-empty; otherwise true (a live object).")
        self.line("public static function truthy(x:Dynamic):Bool {")
        self.indent_level += 1
        self.line("if (x == null) return false;")
        self.line("if (Std.isOfType(x, Bool)) return (x : Bool);")
        self.line("if (Std.isOfType(x, Float)) return (x : Float) != 0;")
        self.line("if (Std.isOfType(x, Int)) return (x : Int) != 0;")
        self.line("if (Std.isOfType(x, String)) return (x : String).length > 0;")
        self.line("if (Std.isOfType(x, Array)) return (x : Array<Dynamic>).length > 0;")
        self.line("if (Std.isOfType(x, haxe.Constraints.IMap)) "
                  "return (x : haxe.Constraints.IMap<Dynamic, Dynamic>)"
                  ".keys().hasNext();")
        self.line("return true;")
        self.indent_level -= 1
        self.line("}")
        self.indent_level -= 1
        self.line("}")
        return self.output()

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

    def _scan_imported_modules(self, module_node):
        # Cross-module type info: for each `import x` / `from x import ...`,
        # locate the sibling `x.py` and scan its classes and free functions
        # into our registries. This is type-info only (no emission); it lets
        # type-directed logic (truthiness, .values()/.keys(), subscript,
        # membership, return-kind inference, constructor `new`) work on types
        # defined in another module. Without it, an imported field/param like
        # `state.nodes: Dict[...]` or `node.expr_left: str` is invisible and
        # mis-emits. Failures (missing file, parse error) are ignored — the
        # emitter degrades to the prior behavior for that module.
        if not self._source_dir:
            return
        import os
        modules = set()
        for stmt in module_node.body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    if alias.name not in self.SILENT_IMPORT_MODULES \
                            and alias.name not in self.BUILTIN_MODULES:
                        modules.add(alias.name)
            elif isinstance(stmt, ast.ImportFrom):
                if stmt.module and not stmt.level \
                        and stmt.module not in self.SILENT_IMPORT_MODULES \
                        and stmt.module not in self.BUILTIN_MODULES:
                    modules.add(stmt.module)
        for mod in modules:
            path = os.path.join(self._source_dir, mod.replace(".", os.sep) + ".py")
            if not os.path.isfile(path):
                continue
            try:
                f = open(path, "r")
                try:
                    src = f.read()
                finally:
                    f.close()
                imported_tree = ast.parse(src, filename=path)
            except (OSError, SyntaxError):
                continue
            # Only register types/signatures; do not recurse emission or
            # transitively pull in their imports (we want the directly named
            # module's surface). Local definitions are scanned afterward and
            # overwrite on clash.
            self._scan_classes(imported_tree)
            self._scan_functions(imported_tree)

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
            fields = set()
            # Map field name -> its declared annotation node, so attribute
            # accesses can be type-directed (truthiness, subscript, etc.).
            field_annotations = {}
            # Fields explicitly declared at class scope (AnnAssign). Used to
            # decide which __init__-assigned fields still need an auto-
            # generated declaration (Haxe requires every field declared).
            declared_fields = set()
            for item in stmt.body:
                if isinstance(item, ast.FunctionDef):
                    methods.add(item.name)
                    method_signatures[item.name] = self._build_signature(item)
                    # `@property` getters expose a field-like attribute; record
                    # the getter's return annotation as the field's type so
                    # `obj.prop` accesses are type-directed (e.g.
                    # `state.selected_node -> Optional[UINode]`).
                    if self._has_decorator(item, "property") \
                            and item.returns is not None:
                        field_annotations.setdefault(item.name, item.returns)
                    # Fields are also bound as `self.x = ...` in __init__;
                    # their declared types come from the matching parameter
                    # annotation when the assignment is `self.x = x`.
                    if item.name == "__init__":
                        param_anns = {}
                        for a in item.args.args:
                            if a.annotation is not None:
                                param_anns[a.arg] = a.annotation
                        for sub in ast.walk(item):
                            if isinstance(sub, ast.Assign):
                                for tgt in sub.targets:
                                    if isinstance(tgt, ast.Attribute) \
                                            and isinstance(tgt.value, ast.Name) \
                                            and tgt.value.id == "self":
                                        fields.add(tgt.attr)
                                        if isinstance(sub.value, ast.Name) \
                                                and sub.value.id in param_anns:
                                            field_annotations.setdefault(
                                                tgt.attr,
                                                param_anns[sub.value.id])
                            elif isinstance(sub, ast.AnnAssign) \
                                    and isinstance(sub.target, ast.Attribute) \
                                    and isinstance(sub.target.value, ast.Name) \
                                    and sub.target.value.id == "self":
                                # `self.x: T = value` annotates an instance
                                # field with its declared type.
                                fields.add(sub.target.attr)
                                if sub.annotation is not None:
                                    field_annotations.setdefault(
                                        sub.target.attr, sub.annotation)
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.add(item.target.id)
                    declared_fields.add(item.target.id)
                    if item.annotation is not None:
                        field_annotations[item.target.id] = item.annotation
                    # `NAME: T = value` at class scope is a class variable
                    # (static), distinct from a bare instance-field declaration.
                    if item.value is not None:
                        self.class_statics.setdefault(stmt.name, set()).add(item.target.id)
                elif isinstance(item, ast.Assign):
                    # `NAME = value` at class scope -> Python class variable
                    # -> Haxe static. Record an inferred kind so member access
                    # / iteration over the static is type-directed.
                    for tgt in item.targets:
                        if isinstance(tgt, ast.Name):
                            self.class_statics.setdefault(stmt.name, set()).add(tgt.id)
                            k = self._infer_value_kind(item.value)
                            if k is not None:
                                self.class_static_kinds[(stmt.name, tgt.id)] = k
            self.classes[stmt.name] = {
                "bases": bases,
                "methods": methods,
                "method_signatures": method_signatures,
                "fields": fields,
                "field_annotations": field_annotations,
                "declared_fields": declared_fields,
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
        # ClassName.method(...) -> static/class method on a known class
        # (incl. @staticmethod). Resolves positional args against the
        # method's options/positional signature like any other call.
        if isinstance(func, ast.Attribute) \
                and isinstance(func.value, ast.Name) \
                and func.value.id in self.classes:
            cls = self.classes[func.value.id]
            sig = cls["method_signatures"].get(func.attr)
            if sig is not None:
                return (sig, "method", func.attr)
        # module.func(...) -> free function in an imported module (Milestone
        # 8 type-loading). The module's free functions were scanned into
        # self.functions; resolve by name so return-type/kwarg info is known.
        if isinstance(func, ast.Attribute) \
                and isinstance(func.value, ast.Name) \
                and func.value.id in self.imported_modules:
            sig = self.functions.get(func.attr)
            if sig is not None:
                return (sig, "function", func.attr)
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

    def _class_has_member(self, class_name, attr):
        # True if class_name or any ancestor declares attr as a field or
        # method.
        cls = self.classes.get(class_name)
        if cls is None:
            return False
        if attr in cls["methods"] or attr in cls.get("fields", set()):
            return True
        for base_name in cls["bases"]:
            if self._class_has_member(base_name, attr):
                return True
        return False

    def _subclass_has_member(self, base_name, attr):
        # True if any (transitive) subclass of base_name declares attr.
        # Used to detect the tagged-union downcast idiom: a base-typed
        # variable whose attr lives only on a subclass.
        for name, info in self.classes.items():
            if name == base_name:
                continue
            if attr in info["methods"] or attr in info.get("fields", set()):
                if self._is_subclass_of(name, base_name):
                    return True
        return False

    def _is_subclass_of(self, name, ancestor):
        cls = self.classes.get(name)
        if cls is None:
            return False
        for base_name in cls["bases"]:
            if base_name == ancestor or self._is_subclass_of(base_name, ancestor):
                return True
        return False

    def _field_annotation_of_class(self, class_name, attr):
        # Return the annotation AST node for `attr` on class_name, searching
        # the class itself, its ancestors, and — for the tagged-union
        # downcast idiom (a base-typed receiver whose attr lives only on a
        # subclass) — its subclasses. Returns None when unknown.
        cls = self.classes.get(class_name)
        if cls is None:
            return None
        anns = cls.get("field_annotations", {})
        if attr in anns:
            return anns[attr]
        for base_name in cls["bases"]:
            found = self._field_annotation_of_class(base_name, attr)
            if found is not None:
                return found
        # Downcast case: the attr is declared only on a subclass.
        for name, info in self.classes.items():
            if name == class_name:
                continue
            if attr in info.get("field_annotations", {}) \
                    and self._is_subclass_of(name, class_name):
                return info["field_annotations"][attr]
        return None

    def _field_kind_of_class(self, class_name, attr):
        # Type-kind tuple of `attr` on class_name (str/array/map/...), or
        # None. Drives type-directed truthiness/membership on attributes.
        k = self.class_static_kinds.get((class_name, attr))
        if k is not None:
            return k
        ann = self._field_annotation_of_class(class_name, attr)
        return self._type_kind_of(ann)

    # === Statements ===

    # Modules whose imports are tooling-only and should be silently
    # dropped during emission. typing imports vanish (the type machinery
    # is consumed during emission, not imported in Haxe). The discipline
    # module provides @haxe_extern, also tooling-only. __future__ is
    # Python-version compatibility, not relevant.
    SILENT_IMPORT_MODULES = {"typing", "__future__", "discipline",
                             "pyhaxe.discipline", "haxe_extern"}

    # Top-level packages that are pure backend/platform glue: they exist only
    # to implement `@haxe_extern` class bodies (e.g. GUI4GUI's Kivy backend in
    # kit.py), which the emitter drops anyway. Importing them as Haxe modules
    # would emit dangling `import App;`/`import Boxlayout;` for types that have
    # no portable Haxe equivalent and are never referenced outside the dropped
    # extern bodies. We drop these imports entirely. Like BUILTIN_MODULES this
    # is a pragmatic, hard-coded list; the principled replacement is per-target
    # hand-written externs for the backend types the extern signatures expose.
    BACKEND_IMPORT_PACKAGES = {"kivy"}

    # Non-portable Python stdlib modules with no 1:1 Haxe class. Their
    # imports are dropped (no `import Random;`) and their member calls are
    # rewritten inline to Haxe-native equivalents in expr_Call (see
    # _emit_builtin_module_call). This is a pragmatic, hard-coded stopgap;
    # the principled replacement is the @haxe_extern wrapper story
    # (Milestone 4) — a disciplined `random.py` wrapper marked @haxe_extern
    # whose backend is hand-written Haxe. The same wrapper mechanism is how
    # GUI4GUI's own kit @haxe_extern backend plugs in.
    BUILTIN_MODULES = {"random", "time"}

    def _is_backend_import(self, module):
        # True if `module` belongs to a backend/platform package whose imports
        # exist only to implement dropped @haxe_extern bodies (e.g. `kivy.app`,
        # `kivy.uix.button`). Matches the package root and any submodule.
        if module is None:
            return False
        root = module.split(".", 1)[0]
        return root in self.BACKEND_IMPORT_PACKAGES

    def stmt_ImportFrom(self, node):
        # Drop typing/tooling imports silently.
        if node.module in self.SILENT_IMPORT_MODULES:
            return
        # Drop backend-only (extern-implementing) imports silently, but
        # remember the local names so any that leak into extern signatures
        # get an `extern class` stub at flush time.
        if self._is_backend_import(node.module):
            for alias in node.names:
                self._backend_type_names.add(alias.asname or alias.name)
            return
        # Cross-module `from x import y [as z]` (Milestone 8). The Python
        # module `x` maps to a Haxe class `X` whose top-level functions /
        # constants are statics. Each imported name binds:
        #   - an UpperCamelCase name (a class) -> a top-level Haxe type
        #     accessible bare (same classpath root package): no rewrite.
        #   - a lowercase name (function / constant) -> `X.name`, a static
        #     reference, recorded in imported_names so bare uses resolve.
        # We emit a real `import X;` so the module class (and its sibling
        # types) are in scope.
        if node.module is None or node.level:
            # Relative imports (`from . import x`) aren't modeled; mark them.
            names = ", ".join(self._format_alias(a) for a in node.names)
            self.line("// import: from " + str(node.module) + " import " + names)
            return
        module_class = self._module_to_class_name(node.module)
        self._add_haxe_import(module_class)
        for alias in node.names:
            local = alias.asname or alias.name
            if alias.name and alias.name[0].isupper():
                # Imported class: top-level Haxe type, used bare. If aliased
                # to a different name, fall back to a comment (Haxe `import
                # X.Y as Z` needs the originating module type path, which we
                # don't track here) — disciplined code rarely aliases types.
                if alias.asname and alias.asname != alias.name:
                    self.line("// import alias unsupported: " + alias.name +
                              " as " + alias.asname)
                continue
            # Function/constant: bare local name resolves to `X.name`.
            self.imported_names[local] = module_class + "." + alias.name

    def stmt_Import(self, node):
        # Cross-module `import x [as y]` (Milestone 8). Register the module
        # -> Haxe class mapping so qualified refs `x.func(...)` resolve to
        # `X.func(...)` (see expr_Attribute), and emit a real Haxe import so
        # the module class is in scope.
        emitted = []
        for alias in node.names:
            if alias.name in self.SILENT_IMPORT_MODULES:
                continue
            if self._is_backend_import(alias.name):
                self._backend_type_names.add(alias.asname or alias.name)
                continue
            if alias.name in self.BUILTIN_MODULES:
                # Non-portable stdlib: no Haxe import; record the local name
                # so `random.fn(...)` is rewritten inline (expr_Call).
                local = alias.asname or alias.name
                self.builtin_module_aliases[local] = alias.name
                continue
            module_class = self._module_to_class_name(alias.name)
            local = alias.asname or alias.name
            self.imported_modules[local] = module_class
            emitted.append(module_class)
        for module_class in emitted:
            self._add_haxe_import(module_class)

    def _add_haxe_import(self, module_class):
        # Buffer an `import X;` (deduped) for emission at the top of the
        # file. A module importing itself (rare) is skipped.
        if module_class == self.module_class_name:
            return
        if module_class not in self._haxe_imports:
            self._haxe_imports.append(module_class)

    def _format_alias(self, alias):
        if alias.asname:
            return alias.name + " as " + alias.asname
        return alias.name

    def stmt_Expr(self, node):
        # Standalone expression as statement (docstring, side-effect call).
        # Strip docstrings (string-only Expr).
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return
        # `arr.extend(iterable)` has no Haxe Array method; Python uses it
        # for its side effect (append all). Emit a for-push loop. Only the
        # statement form is supported (extend returns None in Python, so it
        # never appears in expression position in disciplined code).
        call = node.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) \
                and call.func.attr == "extend" and len(call.args) == 1 \
                and not call.keywords:
            receiver = self.emit_expr(call.func.value)
            iterable = self._emit_iterable(call.args[0])
            tmp = self._fresh_tmp()
            self.line("for (" + tmp + " in " + iterable + ") " +
                      receiver + ".push(" + tmp + ");")
            return
        self.line(self.emit_expr(node.value) + ";")

    def _fresh_tmp(self):
        # Generate a unique temporary loop-variable name for desugared
        # comprehensions / extend loops, avoiding collisions.
        n = getattr(self, "_tmp_counter", 0)
        self._tmp_counter = n + 1
        return "_g" + str(n)

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
        self.line("function " + self._safe_ident(node.name) + "(" + params + "):" + ret + " {")
        self.indent_level += 1
        prev_var_types = dict(self.var_types)
        prev_declared = self.declared_vars
        prev_local_funcs = self.local_functions
        self.local_functions = dict(prev_local_funcs)
        self.declared_vars = set()
        self._return_kind = self._type_kind_of(node.returns)
        self._register_param_var_types(node.args)
        self._register_local_functions(node.body)
        self._hoist_branch_locals(node.body)
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.var_types = prev_var_types
        self.declared_vars = prev_declared
        self.local_functions = prev_local_funcs
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _hoist_branch_locals(self, body):
        # Python lets a name first-bound inside an if/try/for/while branch be
        # read after that block; Haxe's `var` is block-scoped, so such a name
        # would be undefined at the read site. Detect names that are
        # *only* assigned inside nested branches of this body (never bound at
        # the body's own top level) yet read somewhere at this level, and
        # emit a leading `var name;` so the branch assignments populate one
        # enclosing binding. We then mark them declared so the in-branch
        # assignment emits without a (shadowing) `var`. Conservative: only
        # plain `name = ...` targets are considered.
        top_assigned = set()
        nested_assigned = set()
        used = set()

        def assigned_names(stmt):
            names = set()
            if isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                names.add(stmt.target.id)
            return names

        for stmt in body:
            top_assigned |= assigned_names(stmt)
        for stmt in body:
            if isinstance(stmt, (ast.If, ast.For, ast.While, ast.Try)):
                for sub in ast.walk(stmt):
                    if sub is stmt:
                        continue
                    nested_assigned |= assigned_names(sub)
        for stmt in body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    used.add(sub.id)
        hoist = []
        for name in nested_assigned:
            if name in top_assigned:
                continue
            if name in self.declared_vars:
                continue
            if name not in used:
                continue
            hoist.append(name)
        # Stable order: by first appearance among nested assignments.
        for name in sorted(hoist):
            self.line("var " + name + ";")
            self.declared_vars.add(name)

    def _register_local_functions(self, body):
        # Record the return-type kind of any nested `def` directly in this
        # body, so calls to them resolve a static kind (drives boolean-context
        # coercion of the result — e.g. a nested function declared `-> bool`
        # vs `-> Optional[_BackEdge]`). Only the immediate body is scanned;
        # the registry is scoped to the enclosing function and restored on
        # exit.
        for stmt in body:
            if isinstance(stmt, ast.FunctionDef):
                self.local_functions[stmt.name] = self._type_kind_of(stmt.returns)

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
        # Bare `str`, or a known class name (Name or forward-ref string).
        if isinstance(type_node, ast.Name):
            if type_node.id == "str":
                return ("str",)
            if type_node.id == "bool":
                return ("bool",)
            if type_node.id in ("list", "List"):
                return ("array",)
            if type_node.id in ("dict", "Dict"):
                return ("map",)
            if type_node.id in ("set", "Set"):
                return ("set",)
            if type_node.id in self.classes:
                return ("class", type_node.id)
            return None
        if isinstance(type_node, ast.Constant) and isinstance(type_node.value, str):
            if type_node.value == "str":
                return ("str",)
            if type_node.value in self.classes:
                return ("class", type_node.value)
            return None
        # Generic subscripts: List[...] -> array, Dict[...] -> map.
        if isinstance(type_node, ast.Subscript):
            base = type_node.value.id if isinstance(type_node.value, ast.Name) else None
            if base in ("List", "list"):
                # Capture the element kind so iterating types the loop var.
                slice_node = type_node.slice
                if hasattr(ast, "Index") and isinstance(slice_node, ast.Index):
                    slice_node = slice_node.value
                ek = self._type_kind_of(slice_node)
                return ("array", ek) if ek is not None else ("array",)
            if base in ("Dict", "dict"):
                # Capture the value-type kind (Dict[K, V] -> ("map", kind(V)))
                # so `m.get(k)` / iteration know the element kind.
                slice_node = type_node.slice
                if hasattr(ast, "Index") and isinstance(slice_node, ast.Index):
                    slice_node = slice_node.value
                if isinstance(slice_node, ast.Tuple) and len(slice_node.elts) == 2:
                    vk = self._type_kind_of(slice_node.elts[1])
                    if vk is not None:
                        return ("map", vk)
                return ("map",)
            # A set is modeled as a Map<T, Bool>; track it as a map but
            # remember it's a set so `.add` and membership pick set forms.
            if base in ("Set", "set"):
                return ("set",)
            # Optional[Class] / Null[Class] -> the underlying class kind.
            if base in ("Optional", "Null"):
                slice_node = type_node.slice
                if hasattr(ast, "Index") and isinstance(slice_node, ast.Index):
                    slice_node = slice_node.value
                return self._type_kind_of(slice_node)
        # PEP 604 `Class | None` -> the underlying class kind.
        if isinstance(type_node, ast.BinOp) and isinstance(type_node.op, ast.BitOr):
            elts = self._flatten_union_binop(type_node)
            non_none = [e for e in elts if not self._is_none_constant(e)]
            if len(non_none) == 1:
                return self._type_kind_of(non_none[0])
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
        # List literal -> array. Record the element kind when the elements are
        # uniform tuple literals, so iterating the array types the loop var as
        # a tuple (`pair[0]` -> `pair._0`).
        if isinstance(value_node, ast.List):
            if value_node.elts and all(isinstance(e, ast.Tuple) for e in value_node.elts):
                arities = {len(e.elts) for e in value_node.elts}
                if len(arities) == 1:
                    return ("array", ("tuple", arities.pop()))
            # Uniform element kind (e.g. all str-typed fields) -> typed array,
            # so iterating it types the loop var.
            if value_node.elts:
                elem_kinds = [self._static_kind(e) for e in value_node.elts]
                if elem_kinds[0] is not None \
                        and all(k == elem_kinds[0] for k in elem_kinds):
                    return ("array", elem_kinds[0])
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
        # `m.get(k)` on a map whose value-kind we track -> that element kind
        # (e.g. `n = state.nodes.get(id)` makes n a UINode, so `not n` becomes
        # a null check). Plain `m[k]` (Subscript) handled above.
        if isinstance(value_node, ast.Call) \
                and isinstance(value_node.func, ast.Attribute) \
                and value_node.func.attr == "get" and len(value_node.args) >= 1:
            recv_kind = self._static_kind(value_node.func.value)
            if recv_kind is not None and recv_kind[0] == "map" and len(recv_kind) > 1:
                return recv_kind[1]
        # Call to a known free function -> kind of its declared return type
        # (e.g. `tokens = tokenize(text)` where tokenize -> List[Token]).
        if isinstance(value_node, ast.Call) and isinstance(value_node.func, ast.Name):
            sig = self.functions.get(value_node.func.id)
            if sig is not None:
                return self._type_kind_of(sig.get("returns"))
        # Call to a known method (self.m(...) or ClassName.m(...)) -> its
        # declared return-type kind (e.g. `box = LayoutEngine.resolve(...)`
        # gives box a tuple kind, so `box[0]` becomes `box._0`).
        if isinstance(value_node, ast.Call):
            sig, _kind, _name = self._lookup_signature(value_node)
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

    def _module_to_class_name(self, module_name):
        # Map a Python module name to a Haxe class/module name (Milestone 8).
        # snake_case -> UpperCamelCase: `constraint_eval` -> `ConstraintEval`,
        # `editor_core` -> `EditorCore`. A dotted package path (`pkg.mod`)
        # uses the final component. Names that are already valid Haxe type
        # identifiers (start uppercase) are capitalized componentwise too so
        # the mapping is idempotent for `ConstraintEval` -> `ConstraintEval`.
        base = module_name.split(".")[-1]
        parts = [p for p in base.split("_") if p]
        if not parts:
            return self._capitalize(base)
        return "".join(self._capitalize(p) for p in parts)

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
        self._return_kind = self._type_kind_of(node.returns)
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

        # Auto-declare fields that are only bound via `self.x = ...` in
        # __init__ (no class-level annotation). Haxe requires every field
        # to be declared; Python infers them from the assignment. Skip
        # fields inherited from a base and those already declared at class
        # scope (those emit via their AnnAssign statement below).
        if cls_info is not None:
            declared = cls_info.get("declared_fields", set())
            base_names = cls_info.get("bases", [])
            for fname in sorted(cls_info.get("fields", set())):
                if fname in declared:
                    continue
                if any(self._class_has_member(b, fname) for b in base_names):
                    continue
                ann = cls_info.get("field_annotations", {}).get(fname)
                if self._is_private_name(fname):
                    vis = "private"
                    out_name = self._strip_private_underscores(fname)
                else:
                    vis = "public"
                    out_name = fname
                type_str = self.emit_type(ann) if ann is not None else "Dynamic"
                self.line(vis + " var " + out_name + ":" + type_str + ";")

        # F3: collect `@<name>.setter` methods so the matching `@property`
        # getter can emit a full `var name(get, set)` property with both a
        # get_name and set_name accessor, instead of a read-only
        # `(get, never)` plus a colliding same-named setter method.
        property_setters = {}
        for stmt in node.body:
            if isinstance(stmt, ast.FunctionDef):
                setter_for = self._property_setter_target(stmt)
                if setter_for is not None:
                    property_setters[setter_for] = stmt
        self._property_setters = property_setters

        for stmt in node.body:
            # Skip docstrings inside class bodies.
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                    and isinstance(stmt.value.value, str):
                continue
            # Setter methods are emitted by their paired property getter; skip
            # them here so they don't also emit as a standalone method (F3).
            if isinstance(stmt, ast.FunctionDef) \
                    and self._property_setter_target(stmt) is not None:
                continue
            self.emit_stmt(stmt)
        self._property_setters = {}

        # Fold in module-level constants and free functions when this class's
        # name collides with the module holder name (see emit_module).
        if self._merge_into_class == node.name:
            if self._merge_consts:
                self.line("")
                for stmt in self._merge_consts:
                    self._emit_module_constant(stmt)
            for func in self._merge_functions:
                self.line("")
                self._emit_static_function_in_class(func)

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
        # Python `@property` getter -> a Haxe read-only property backed by a
        # `get_<name>` accessor, so callers read `obj.name` (no parens) just
        # like in Python.
        if self._has_decorator(node, "property"):
            self._emit_property_getter(node)
            return
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
        prev_local_funcs = self.local_functions
        self.local_functions = dict(prev_local_funcs)
        self.declared_vars = set()
        self._return_kind = self._type_kind_of(node.returns)
        if not uses_options:
            self._register_param_var_types(node.args)
        # For options methods, emit the destructuring prelude first so
        # the rest of the body can use the parameter names as locals.
        if uses_options:
            self._emit_options_prelude(signature)
        self._register_local_functions(node.body)
        self._hoist_branch_locals(node.body)
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.in_class = prev_in_class
        self.var_types = prev_var_types
        self.declared_vars = prev_declared
        self.local_functions = prev_local_funcs
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _property_setter_target(self, node):
        # If `node` is a `@<name>.setter`-decorated method, return <name>;
        # else None. Python spells a property setter as
        # `@prop.setter def prop(self, value): ...`, which parses to a
        # decorator `Attribute(value=Name('prop'), attr='setter')`.
        for dec in node.decorator_list:
            if isinstance(dec, ast.Attribute) and dec.attr == "setter" \
                    and isinstance(dec.value, ast.Name):
                return dec.value.id
        return None

    def _emit_property_getter(self, node):
        # Python `@property` (+ optional `@name.setter`) -> a Haxe property.
        # With no setter: `var name(get, never)` + `get_name` (read-only).
        # With a setter (F3): `var name(get, set)` + `get_name` + `set_name`,
        # so `this.name = value` resolves to the setter instead of failing
        # with "cannot be accessed for writing".
        is_private = self._is_private_name(node.name)
        visibility = "private" if is_private else "public"
        name = self._strip_private_underscores(node.name)
        ret = self.emit_type(node.returns)
        setter = getattr(self, "_property_setters", {}).get(node.name)
        access = "set" if setter is not None else "never"
        self.line(visibility + " var " + name + "(get, " + access + "):" + ret + ";")
        self.line(visibility + " function get_" + name + "():" + ret + " {")
        self._emit_accessor_body(node, ret)
        if setter is not None:
            self._emit_property_setter(setter, name, visibility)

    def _emit_property_setter(self, node, name, visibility):
        # Emit `function set_<name>(value:T):T { ...body...; return value; }`
        # for a `@name.setter`. Haxe property setters must return the assigned
        # value's type; the Python setter returns None, so we mirror the
        # incoming parameter type and append `return <param>;`.
        non_self = [a for a in node.args.args if a.arg not in ("self", "cls")]
        param = non_self[0] if non_self else None
        value_name = param.arg if param is not None else "value"
        value_type = self.emit_type(param.annotation) \
            if param is not None and param.annotation is not None else "Dynamic"
        self.line(visibility + " function set_" + name +
                  "(" + value_name + ":" + value_type + "):" + value_type + " {")
        self.indent_level += 1
        prev_in_class = self.in_class
        self.in_class = False
        prev_var_types = dict(self.var_types)
        prev_declared = self.declared_vars
        prev_local_funcs = self.local_functions
        self.local_functions = dict(prev_local_funcs)
        self.declared_vars = set()
        self._return_kind = None
        self._register_param_var_types(node.args)
        self._register_local_functions(node.body)
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.line("return " + value_name + ";")
        self.in_class = prev_in_class
        self.var_types = prev_var_types
        self.declared_vars = prev_declared
        self.local_functions = prev_local_funcs
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _emit_accessor_body(self, node, ret):
        self.indent_level += 1
        prev_in_class = self.in_class
        self.in_class = False
        prev_var_types = dict(self.var_types)
        prev_declared = self.declared_vars
        prev_local_funcs = self.local_functions
        self.local_functions = dict(prev_local_funcs)
        self.declared_vars = set()
        self._return_kind = self._type_kind_of(node.returns)
        self._register_param_var_types(node.args)
        self._register_local_functions(node.body)
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.in_class = prev_in_class
        self.var_types = prev_var_types
        self.declared_vars = prev_declared
        self.local_functions = prev_local_funcs
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
        expr = self.emit_expr(node.value)
        # Tagged-union downcast on return: if the declared return type is a
        # subclass but we're returning a base-typed value (narrowed via a
        # `kind` check in Python), insert a typed cast so Haxe accepts it.
        if self._return_kind is not None and self._return_kind[0] == "class" \
                and isinstance(node.value, ast.Name):
            val_info = self.var_types.get(node.value.id)
            if val_info is not None and val_info[0] == "class":
                ret_cls = self._return_kind[1]
                val_cls = val_info[1]
                if val_cls != ret_cls and self._is_subclass_of(ret_cls, val_cls):
                    expr = "cast(" + expr + ", " + ret_cls + ")"
        self.line("return " + expr + ";")

    def stmt_AnnAssign(self, node):
        target = self.emit_expr(node.target)
        # An annotated assignment to an attribute (`self.x: T = value`) inside a
        # method body is just a field write — the field is already declared at
        # class scope (auto-declared by the scanner). Emit a plain assignment,
        # never `var this.x`.
        if isinstance(node.target, ast.Attribute):
            if node.value is None:
                return
            self.line(target + " = " + self.emit_expr(node.value) + ";")
            return
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
            # `NAME: T = value` at class scope is a class variable (static).
            # A bare `NAME: T` (no value) is an instance-field declaration.
            is_static = node.value is not None and isinstance(node.target, ast.Name)
            prefix = visibility + (" static var " if is_static else " var ")
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
        # A bare-Name assignment at class-body scope (not inside a method) is
        # a Python class variable — shared, so a Haxe `static var`. Visibility
        # follows the leading-underscore convention; the underscore is stripped
        # and the static is recorded so `self.NAME` reads resolve to
        # `ClassName.NAME` (Haxe forbids instance access to statics).
        if self.in_class and isinstance(target_node, ast.Name):
            raw = target_node.id
            if self._is_private_name(raw):
                out_name = self._strip_private_underscores(raw)
                vis = "private"
            else:
                out_name = raw
                vis = "public"
            self.class_statics.setdefault(self.current_class_name, set()).add(raw)
            value = self.emit_expr(node.value)
            self.line(vis + " static var " + out_name + " = " + value + ";")
            return
        target = self.emit_expr(target_node)
        value = self.emit_expr(node.value)
        # Haxe `Math.max`/`Math.min` etc. always return Float; Python `max`/
        # `min` over ints stay int. When the assignment target is declared
        # `int`, wrap a Float-producing RHS in `Std.int(...)` so it fits.
        if self._is_int_target(target_node) and self._yields_float(node.value):
            value = "Std.int(" + value + ")"
        prefix = "var " if is_new_local else ""
        self.line(prefix + target + " = " + value + ";")

    def _is_int_target(self, target_node):
        # True if the assignment target is a known `int`-typed attribute/var.
        if isinstance(target_node, ast.Attribute):
            recv = self._static_kind(target_node.value)
            if recv is not None and recv[0] == "class":
                ann = self._field_annotation_of_class(recv[1], target_node.attr)
                return isinstance(ann, ast.Name) and ann.id == "int"
        return False

    def _yields_float(self, value_node):
        # True if the RHS produces a Haxe Float that an int target would
        # reject — Math.max/min/abs/round/random calls, or an expression
        # containing one. Conservative: covers the constructs the emitter maps
        # to Math.* (Python max/min/abs over numbers).
        for sub in ast.walk(value_node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                    and sub.func.id in ("max", "min"):
                return True
        return False

    def stmt_AugAssign(self, node):
        target = self.emit_expr(node.target)
        value = self.emit_expr(node.value)
        op = AUGASSIGN_MAP.get(type(node.op))
        if op is None:
            self.line("// TODO augassign: " + type(node.op).__name__)
            return
        self.line(target + " " + op + " " + value + ";")

    def stmt_Delete(self, node):
        # `del m[k]`  -> `m.remove(k);`   (Map.remove / StringMap.remove)
        # `del list[i]` -> `list.splice(i, 1);`  (Array element removal)
        # Type-directed: pick remove vs splice from the receiver's kind.
        # Disciplined Python deletes one subscript target at a time.
        for target in node.targets:
            slice_node = None
            if isinstance(target, ast.Subscript):
                slice_node = target.slice
                if hasattr(ast, "Index") and isinstance(slice_node, ast.Index):
                    slice_node = slice_node.value
            if slice_node is None or isinstance(slice_node, ast.Slice):
                self.line("// TODO stmt: Delete " + type(target).__name__)
                continue
            receiver = self.emit_expr(target.value)
            index = self.emit_expr(slice_node)
            kind = self._static_kind(target.value)
            if kind is not None and kind[0] == "array":
                self.line(receiver + ".splice(" + index + ", 1);")
            else:
                # Maps (and the common case) use remove(key).
                self.line(receiver + ".remove(" + index + ");")

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
        cond = self._emit_test(node.test)
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
        cond = self._emit_test(node.test)
        self.line("while (" + cond + ") {")
        self.indent_level += 1
        self._emit_block_body(node.body)
        self.indent_level -= 1
        self.line("}")

    def stmt_For(self, node):
        # Disciplined Python: iter is a `range(...)` call or a collection; the
        # target is a Name, except `for k, v in m.items()` which unpacks a
        # (key, value) pair -> Haxe key-value iteration `for (k => v in m)`.
        kv = self._items_kv_target(node.target, node.iter)
        if kv is not None:
            self.line("for (" + kv + ") {")
            self.indent_level += 1
            keys = set()
            if isinstance(node.target, ast.Tuple):
                keys = {e.id for e in node.target.elts if isinstance(e, ast.Name)}
            self._emit_block_body(node.body, extra_declared=keys)
            self.indent_level -= 1
            self.line("}")
            return
        target = self.emit_expr(node.target)
        iter_expr = self._format_for_iter(node.iter)
        self.line("for (" + target + " in " + iter_expr + ") {")
        self.indent_level += 1
        # The loop variable is declared by the `for` header in Haxe, so
        # reassignments to it inside the body must not re-emit `var`.
        extra = set()
        saved_kind = None
        had_kind = False
        if isinstance(node.target, ast.Name):
            extra.add(node.target.id)
            # If the iterable is an array with a known element kind, type the
            # loop var so element ops (e.g. tuple `pair[0]` -> `pair._0`)
            # dispatch correctly inside the body.
            iter_kind = self._static_kind(node.iter)
            elem_kind = iter_kind[1] if iter_kind is not None \
                and iter_kind[0] == "array" and len(iter_kind) > 1 else None
            had_kind = node.target.id in self.var_types
            saved_kind = self.var_types.get(node.target.id)
            if elem_kind is not None:
                self.var_types[node.target.id] = elem_kind
        self._emit_block_body(node.body, extra_declared=extra)
        if isinstance(node.target, ast.Name):
            if had_kind:
                self.var_types[node.target.id] = saved_kind
            else:
                self.var_types.pop(node.target.id, None)
        self.indent_level -= 1
        self.line("}")

    def _items_kv_target(self, target_node, iter_node):
        # If this is `for k, v in MAP.items()`, return the Haxe key-value
        # iteration header `k => v in MAP`. Otherwise None. Haxe Maps support
        # `for (key => value in map)` directly — no `.items()` method exists.
        if not (isinstance(target_node, ast.Tuple) and len(target_node.elts) == 2):
            return None
        if not (isinstance(iter_node, ast.Call)
                and isinstance(iter_node.func, ast.Attribute)
                and iter_node.func.attr == "items" and not iter_node.args):
            return None
        k = self.emit_expr(target_node.elts[0])
        v = self.emit_expr(target_node.elts[1])
        receiver = self.emit_expr(iter_node.func.value)
        return k + " => " + v + " in " + receiver

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
        # `.values()`/`.keys()` on a Map, or a bare Map (Python iterates its
        # keys, Haxe iterates its values), need type-directed iteration.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and not node.args and node.func.attr in ("values", "keys"):
            return self._emit_iterable(node)
        kind = self._static_kind(node)
        if kind is not None and kind[0] in ("map", "set"):
            return self._emit_iterable(node)
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

    def _safe_ident(self, name):
        # Rename a Python identifier that collides with a Haxe reserved word /
        # builtin function so it doesn't bind to the builtin. Applied
        # consistently at definition and use sites.
        if name in HAXE_RESERVED_IDENTIFIERS:
            return name + "_"
        return name

    def expr_Name(self, node):
        # Disciplined Python uses self for methods; map to Haxe this.
        if node.id == "self":
            return "this"
        # Module-level constants live as static fields on Main. References
        # from other classes must be qualified `Main.NAME`; references from
        # inside Main use the bare name (sibling static field).
        if node.id in self.module_constants and self.current_class_name != self.module_class_name:
            return self.module_class_name + "." + node.id
        # `from x import y` binds a bare name to a static on another module's
        # class (Milestone 8): rewrite the bare local name to `X.y`.
        if node.id in self.imported_names:
            return self.imported_names[node.id]
        # Names like `Exception` need to map to their Haxe equivalents
        # when used as constructor calls (e.g., `raise Exception(...)`).
        # The same map drives type-position translation.
        if node.id in PYTHON_TO_HAXE_TYPES:
            return PYTHON_TO_HAXE_TYPES[node.id]
        return self._safe_ident(node.id)

    def expr_BinOp(self, node):
        # printf-style string formatting (`"%g" % value`) has no operator
        # form in Haxe. We map the common single-substitution numeric/string
        # specifiers to Std.string of the operand — adequate for compact
        # value rendering (the only use in the target code). A literal
        # format with no conversion just passes through.
        if isinstance(node.op, ast.Mod) and self._is_format_string(node.left):
            return self._emit_string_format(node.left, node.right)
        # List concatenation: Python `a + b` on lists -> Haxe `a.concat(b)`
        # (Haxe arrays have no `+` operator). Type-directed: only when both
        # operands are known arrays.
        if isinstance(node.op, ast.Add):
            lk = self._static_kind(node.left)
            rk = self._static_kind(node.right)
            if lk is not None and lk[0] == "array" \
                    and rk is not None and rk[0] == "array":
                return (self.emit_expr(node.left) + ".concat(" +
                        self.emit_expr(node.right) + ")")
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

    def _emit_builtin_module_call(self, module, node):
        # Rewrite a non-portable stdlib member call to Haxe-native code.
        # Returns the Haxe expression, or None if unrecognized (caller falls
        # through to the generic path). Currently only `random`.
        attr = node.func.attr
        if module == "random":
            if attr == "randint" and len(node.args) == 2:
                # Python randint(a, b) is inclusive on both ends; Haxe
                # Std.random(n) yields 0..n-1, so the span is (b - a + 1).
                a = self.emit_expr(node.args[0])
                b = self.emit_expr(node.args[1])
                return ("(" + a + " + Std.random((" + b + ") - (" + a +
                        ") + 1))")
            if attr == "random" and not node.args:
                return "Math.random()"
            if attr == "choice" and len(node.args) == 1:
                seq = self.emit_expr(node.args[0])
                return (seq + "[Std.random(" + seq + ".length)]")
            if attr == "uniform" and len(node.args) == 2:
                a = self.emit_expr(node.args[0])
                b = self.emit_expr(node.args[1])
                return ("(" + a + " + Math.random() * ((" + b + ") - (" +
                        a + ")))")
        if module == "time":
            # time.time() -> seconds since epoch as a Float. Haxe's
            # haxe.Timer.stamp() gives a monotonic-ish seconds Float, which
            # matches the only use here (elapsed-time deltas).
            if attr == "time" and not node.args:
                return "haxe.Timer.stamp()"
        return None

    def expr_JoinedStr(self, node):
        # f-strings. Python `f"...{expr}..."` becomes Haxe string
        # concatenation: literal Constant parts pass through as quoted
        # literals; FormattedValue parts become Std.string(expr) (with
        # optional fixed-precision rounding for a `:.Nf` spec). We use
        # explicit concatenation rather than Haxe single-quote `$`/`${}`
        # interpolation so arbitrary call/format-spec children compose
        # uniformly and the existing quoting/escaping is reused. A pure
        # literal collapses to one string; an all-empty f-string is `""`.
        pieces = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                if part.value:
                    pieces.append(self._haxe_str_literal(part.value))
            else:
                pieces.append(self.emit_expr(part))
        if not pieces:
            return self._haxe_str_literal("")
        # Force a String context when the first piece is a Std.string()/literal
        # already; if every piece is a single formatted value with no literal,
        # Std.string keeps it a String.
        if len(pieces) == 1 and isinstance(node.values[0], ast.Constant):
            return pieces[0]
        return " + ".join(pieces)

    def expr_FormattedValue(self, node):
        # One `{expr[:spec]}` hole inside an f-string. The conversion field
        # (!r/!s/!a) and most of the format spec have no Haxe equivalent and
        # are dropped, except a numeric fixed-precision `:.Nf` which we honor
        # with an explicit round (Haxe has no printf). Everything renders via
        # Std.string so non-string operands stringify.
        spec = self._fstring_fixed_precision(node)
        if spec is not None:
            return self._fmt_fixed(node.value, spec)
        return "Std.string(" + self.emit_expr(node.value) + ")"

    def _fstring_fixed_precision(self, node):
        # Return N for a `:.Nf` format spec on a FormattedValue, else None.
        fmt = node.format_spec
        if not isinstance(fmt, ast.JoinedStr) or len(fmt.values) != 1:
            return None
        part = fmt.values[0]
        if not (isinstance(part, ast.Constant) and isinstance(part.value, str)):
            return None
        s = part.value
        if len(s) >= 3 and s[0] == "." and s[-1] == "f" and s[1:-1].isdigit():
            return int(s[1:-1])
        return None

    def _fmt_fixed(self, value_node, digits):
        # Render `value` to `digits` decimal places as a String. Haxe lacks a
        # printf/toFixed, so we round to the scale and stringify. This is an
        # approximation (no trailing-zero padding); it is only reached on the
        # f-string fallback path in systems.apply_shift, where the value is a
        # drag delta that is later re-parsed. Documented as a known gap; exact
        # formatting should use a disciplined Python-side formatter.
        expr = self.emit_expr(value_node)
        if digits == 0:
            return "Std.string(Math.round(" + expr + "))"
        scale = 10 ** digits
        return ("Std.string(Math.round((" + expr + ") * " + str(scale) +
                ") / " + str(scale) + ")")

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
            return self._infer_value_kind(node) or ("array",)
        if isinstance(node, (ast.ListComp,)):
            return ("array",)
        if isinstance(node, ast.DictComp):
            # Record the value-element kind when it's a set (`{k: set() ...}`),
            # so a later `m[k].add(x)` knows m[k] is a set. Stored as
            # ("map", value_kind).
            vk = self._set_valued_comp_kind(node.value)
            return ("map", vk) if vk is not None else ("map",)
        if isinstance(node, ast.Dict):
            if node.values:
                vk = self._set_valued_comp_kind(node.values[0])
                if vk is not None:
                    return ("map", vk)
            return ("map",)
        if isinstance(node, ast.SetComp):
            return ("map",)
        # sorted(...) yields a new array.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "sorted":
            return ("array",)
        # A call to a function/method/constructor we have a signature for:
        # use its declared return-type kind. This surfaces Bool-returning
        # calls (e.g. `_is_name_start(ch)`, `is_dependent(a, b)`) so a
        # value-context `or`/`and` over them lowers to `||`/`&&` (F1), and
        # class-typed returns so a truthiness test lowers to `!= null` (F4b).
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) \
                    and node.func.id in self.local_functions:
                lk = self.local_functions[node.func.id]
                if lk is not None:
                    return lk
            sig, _kind, _name = self._lookup_signature(node)
            if sig is not None:
                rk = self._type_kind_of(sig.get("returns"))
                if rk is not None:
                    return rk
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
            # Indexing a map whose value-kind we tracked (e.g. a map of sets)
            # yields that element kind, so `m[k].add(x)` picks the set form.
            if base is not None and base[0] == "map" and len(base) > 1:
                return base[1]
        # Attribute access: `recv.attr`. Resolve the receiver's class and
        # look up the field's declared type. Covers both `self.f` and a
        # tracked local of known class type, including base-typed receivers
        # whose attr lives on a subclass (the tagged-union downcast idiom —
        # the same access expr_Attribute routes through `(cast x)`).
        if isinstance(node, ast.Attribute):
            cls_name = None
            if isinstance(node.value, ast.Name):
                if node.value.id == "self":
                    cls_name = self.current_class_name
                else:
                    info = self.var_types.get(node.value.id)
                    if info is not None and info[0] == "class":
                        cls_name = info[1]
            else:
                # Nested access (`self._state.selected_node`): resolve the
                # receiver's kind recursively, then look up the field.
                recv = self._static_kind(node.value)
                if recv is not None and recv[0] == "class":
                    cls_name = recv[1]
            if cls_name is not None:
                return self._field_kind_of_class(cls_name, node.attr)
        # `a + b` where both sides are arrays is itself an array (the result of
        # `.concat`), so chained `a + b + c` keeps each step array-typed.
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            lk = self._static_kind(node.left)
            rk = self._static_kind(node.right)
            if lk is not None and lk[0] == "array" \
                    and rk is not None and rk[0] == "array":
                return ("array",)
        return None

    def _set_valued_comp_kind(self, value_node):
        # Return ("set",) if the dict/map value expression constructs a set
        # (`set()` or a set comprehension/literal), else None. Used to track
        # map-of-set value kinds for type-directed `m[k].add(...)`.
        if isinstance(value_node, ast.Call) and isinstance(value_node.func, ast.Name) \
                and value_node.func.id == "set":
            return ("set",)
        if isinstance(value_node, ast.SetComp) or isinstance(value_node, ast.Set):
            return ("set",)
        return None

    def _emit_test(self, node):
        # Emit an expression used in boolean context (if/while condition,
        # `and`/`or` operand). Python truthiness on a str/array/map means
        # "non-null and non-empty" (maps: non-null); a bare value there is a
        # type error in Haxe. `not x` is already handled by expr_UnaryOp;
        # here we coerce the POSITIVE form. Everything else passes through.
        if isinstance(node, ast.BoolOp):
            # In boolean context, `and`/`or` are short-circuit Bool operators:
            # `&&`/`||` over boolean-coerced operands. (Value context goes
            # through expr_BoolOp, which selects an operand value instead.)
            op = BOOLOP_MAP[type(node.op)]
            my_prec = OPERATOR_PRECEDENCE.get(type(node.op), 0)
            parent_prec = self._prec_stack[-1]
            self._prec_stack.append(my_prec)
            parts = [self._emit_test(v) for v in node.values]
            self._prec_stack.pop()
            result = (" " + op + " ").join(parts)
            return "(" + result + ")" if parent_prec > my_prec else result
        if isinstance(node, (ast.UnaryOp, ast.Compare)):
            # These already yield a Bool (or handle their own coercion).
            return self.emit_expr(node)
        kind = self._static_kind(node)
        if kind is not None and kind[0] == "bool":
            # Already a Bool — no truthiness coercion needed.
            return self.emit_expr(node)
        if kind is not None and kind[0] in ("str", "array"):
            inner = self.emit_expr(node)
            return "(" + inner + " != null && " + inner + ".length > 0)"
        if kind is not None and kind[0] in ("map", "set", "class"):
            # Map/set/object value in boolean context — Python truthiness on a
            # nullable object means "is it present". Haxe rejects a bare
            # Null<T> as Bool, so coerce to a null check. Covers F5 (`if
            # (state)`, `if (artboard)`) and F4b (`... or find_back_edge(...)`,
            # where the nested function returns Null<_BackEdge>).
            inner = self.emit_expr(node)
            return "(" + inner + " != null)"
        # F8: the operand's kind is Dynamic/unknown (Any/object/Optional with no
        # tracked class, an untyped field assigned a Dynamic-returning call,
        # etc.). A bare pass-through here makes hxjava lower `if (dynamic)` to a
        # hard `(java.lang.Boolean) Object` cast that ClassCastExceptions the
        # moment the value isn't a Boolean (it bit the editor's snap-clock:
        # `if self._snap_clock:`, a scheduler handle typed Any). Lower to a
        # null-safe, Python-semantic truthiness instead. The exceptions above
        # (bool/str/array/map/set/class) keep their existing precise coercions;
        # only the genuinely-unknown case routes through the runtime helper.
        if self._is_dynamic_bool_operand(node):
            self._uses_truthy = True
            return "Pyhaxe.truthy(" + self.emit_expr(node) + ")"
        return self.emit_expr(node)

    def _is_dynamic_bool_operand(self, node):
        # True when `node` in boolean context is a Dynamic/unknown VALUE that
        # would otherwise pass through bare (and hit hxjava's Boolean cast). We
        # require an operand SHAPE that denotes a runtime value access — name,
        # attribute, call, or subscript — whose static kind we failed to resolve
        # AND which isn't already known to be a Bool. Literals (numbers, None,
        # True/False) are excluded; they don't suffer the cast. Resolved-Bool
        # expressions (a `-> bool` call like StringTools.startsWith, a bool-typed
        # field/local) are excluded too: they're already valid in bool context,
        # so wrapping them would be needless churn. Anything genuinely unknown is
        # exactly the cast-prone case, and routes through the helper.
        if not isinstance(node, (ast.Name, ast.Attribute, ast.Call,
                                 ast.Subscript)):
            return False
        if self._is_bool_expr(node):
            return False
        return True

    def expr_UnaryOp(self, node):
        # Type-directed truthiness for `not x`. Python `not s` / `not arr`
        # is true when the value is None or empty; Haxe `!s` is a type error
        # on String/Array. Map to an explicit null-or-empty test.
        if isinstance(node.op, ast.Not):
            kind = self._static_kind(node.operand)
            if kind is not None and kind[0] in ("str", "array"):
                inner = self.emit_expr(node.operand)
                return "(" + inner + " == null || " + inner + ".length == 0)"
            if kind is not None and kind[0] in ("map", "set", "class"):
                inner = self.emit_expr(node.operand)
                return "(" + inner + " == null)"
            # F8: `not <dynamic>` — same Boolean-cast hazard as the positive
            # form, plus a bare `!x` on a Dynamic is itself a Haxe type error.
            # Negate the runtime truthiness so `if not x:` is correct for the
            # full range of Python falsey values, not just null.
            if kind is None and self._is_dynamic_bool_operand(node.operand):
                self._uses_truthy = True
                return "!Pyhaxe.truthy(" + self.emit_expr(node.operand) + ")"
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
        # Value context (the BoolOp result is consumed as a value, e.g.
        # `node = a.get(x) or a.get(y)`). Python `or`/`and` yield an OPERAND,
        # not a Bool: `a or b` -> first truthy operand; `a and b` -> last if
        # all truthy else the first falsy one. When every operand is itself a
        # boolean expression, the plain `||`/`&&` form is both correct and
        # idiomatic; otherwise emit a value-selecting ternary chain so the
        # result type is the operand type (not Bool).
        if all(self._is_bool_expr(v) for v in node.values):
            op = BOOLOP_MAP[type(node.op)]
            my_prec = OPERATOR_PRECEDENCE.get(type(node.op), 0)
            parent_prec = self._prec_stack[-1]
            self._prec_stack.append(my_prec)
            parts = [self._emit_test(v) for v in node.values]
            self._prec_stack.pop()
            result = (" " + op + " ").join(parts)
            return "(" + result + ")" if parent_prec > my_prec else result
        # Value-selecting fold, right to left.
        is_or = isinstance(node.op, ast.Or)
        acc = self.emit_expr(node.values[-1])
        for v in reversed(node.values[:-1]):
            val = self.emit_expr(v)
            cond = self._truthy(v, val)
            if is_or:
                acc = "(" + cond + " ? " + val + " : " + acc + ")"
            else:
                acc = "(" + cond + " ? " + acc + " : " + val + ")"
        return acc

    def _is_bool_expr(self, node):
        # True if the node already evaluates to a Bool (so `&&`/`||` are valid
        # without value-selection). Compare/UnaryOp(not)/bool literals qualify;
        # nested BoolOps qualify iff their operands do. A node whose static
        # kind is Bool (a bool-typed name/field/param, or a call to a function
        # or method declared `-> bool`) also qualifies — this is what keeps a
        # value-context `or`/`and` over Bool operands from being lowered to the
        # `(A != null ? A : B)` null-ternary, which the static Java target
        # rejects on a basic Bool (F1).
        if isinstance(node, ast.Compare):
            return True
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return True
        if isinstance(node, ast.BoolOp):
            return all(self._is_bool_expr(v) for v in node.values)
        kind = self._static_kind(node)
        if kind is not None and kind[0] == "bool":
            return True
        return False

    def _truthy(self, node, emitted):
        # A Haxe boolean test for `emitted` matching Python truthiness, used in
        # the value-selecting `or`/`and` fold. str/array -> non-empty; map ->
        # non-null; everything else (objects/nullable) -> non-null.
        kind = self._static_kind(node)
        if kind is not None and kind[0] in ("str", "array"):
            return "(" + emitted + " != null && " + emitted + ".length > 0)"
        return "(" + emitted + " != null)"

    def expr_Compare(self, node):
        # Disciplined Python doesn't use comparison chaining (a < b < c).
        op_type = type(node.ops[0])
        # Membership: `x in container` / `x not in container`. Haxe has no
        # `in` operator, so this is type-directed by the container (the
        # right operand): String -> indexOf != -1, Array -> indexOf != -1,
        # Map/Dict -> exists(key).
        if op_type in (ast.In, ast.NotIn):
            return self._emit_membership(node, op_type is ast.NotIn)
        my_prec = OPERATOR_PRECEDENCE.get(op_type, 0)
        parent_prec = self._prec_stack[-1]
        self._prec_stack.append(my_prec)
        left = self.emit_expr(node.left)
        op = COMPARE_MAP[op_type]
        right = self.emit_expr(node.comparators[0])
        self._prec_stack.pop()
        result = left + " " + op + " " + right
        if parent_prec > my_prec:
            return "(" + result + ")"
        return result

    def _emit_membership(self, node, negate):
        # `elem (not) in container`. Emit type-directed on the container's
        # kind. Falls back to Array/String indexOf form when the kind is
        # unknown (the most common case for untyped collections).
        container = node.comparators[0]
        kind = self._static_kind(container)
        # Operands need their own (high) precedence context so the produced
        # call/comparison composes correctly with surrounding operators.
        self._prec_stack.append(OPERATOR_PRECEDENCE.get(ast.Eq, 4))
        elem_str = self.emit_expr(node.left)
        cont_str = self.emit_expr(container)
        self._prec_stack.pop()
        if kind is not None and kind[0] in ("map", "set"):
            # Map/set membership tests the key set.
            expr = cont_str + ".exists(" + elem_str + ")"
            return "!" + expr if negate else expr
        if kind is not None and kind[0] == "str":
            # Substring search; -1 means absent.
            cmp = "==" if negate else "!="
            return cont_str + ".indexOf(" + elem_str + ") " + cmp + " -1"
        # Array (known or default): indexOf-based membership.
        cmp = "==" if negate else "!="
        return cont_str + ".indexOf(" + elem_str + ") " + cmp + " -1"

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

        # Non-portable stdlib module calls (`random.randint`, ...) rewritten
        # inline to Haxe-native equivalents. See BUILTIN_MODULES.
        if isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in self.builtin_module_aliases:
            rewritten = self._emit_builtin_module_call(
                self.builtin_module_aliases[node.func.value.id], node)
            if rewritten is not None:
                return rewritten

        # hex(n) -> ("0x" + StringTools.hex(n)): Python's hex() prefixes
        # "0x"; we mirror it so a trailing `[2:]` strips the prefix and the
        # value matches. Haxe StringTools.hex yields uppercase digits (case
        # is irrelevant for the color-string use in systems.py).
        if isinstance(node.func, ast.Name) and node.func.id == "hex" \
                and len(node.args) == 1:
            return '("0x" + StringTools.hex(' + self.emit_expr(node.args[0]) + "))"

        # str.rsplit(sep, 1) -> split on the LAST occurrence of sep, yielding
        # a 2-element Array [before, after] (or [s] when sep is absent). Haxe's
        # String has no rsplit; only the maxsplit==1 form has a clean,
        # allocation-light lowering and is the only form disciplined code uses
        # (e.g. `filename.rsplit(".", 1)[0]`). (F7)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "rsplit" \
                and len(node.args) == 2 and not node.keywords \
                and isinstance(node.args[1], ast.Constant) \
                and node.args[1].value == 1:
            receiver = self.emit_expr(node.func.value)
            sep = self.emit_expr(node.args[0])
            return ("(function(_s:String, _sep:String):Array<String> {"
                    " var _i = _s.lastIndexOf(_sep);"
                    " return _i == -1 ? [_s] :"
                    " [_s.substring(0, _i), _s.substring(_i + _sep.length)]; })("
                    + receiver + ", " + sep + ")")

        # str.zfill(w) -> StringTools.lpad(s, "0", w): left-pad with zeros.
        if isinstance(node.func, ast.Attribute) and node.func.attr == "zfill" \
                and len(node.args) == 1:
            receiver = self.emit_expr(node.func.value)
            width = self.emit_expr(node.args[0])
            return 'StringTools.lpad(' + receiver + ', "0", ' + width + ")"

        # Special case: len(x) -> x.length. Python builtin -> Haxe property.
        if isinstance(node.func, ast.Name) and node.func.id == "len" \
                and len(node.args) == 1:
            return self.emit_expr(node.args[0]) + ".length"

        # abs(x) -> Math.abs(x). Python builtin -> Haxe Math.
        if isinstance(node.func, ast.Name) and node.func.id == "abs" \
                and len(node.args) == 1:
            return "Math.abs(" + self.emit_expr(node.args[0]) + ")"

        # print(x) -> trace(x). Haxe's trace is the portable stdout-ish
        # builtin (the JS/interp targets route it to console/stdout).
        if isinstance(node.func, ast.Name) and node.func.id == "print" \
                and not node.keywords:
            inner = ", ".join(self.emit_expr(a) for a in node.args)
            return "trace(" + inner + ")"

        # Reflective attribute access by dynamic name: setattr/getattr ->
        # Reflect.setField / Reflect.field (Haxe's runtime reflection).
        if isinstance(node.func, ast.Name) and node.func.id == "setattr" \
                and len(node.args) == 3 and not node.keywords:
            obj, name, val = (self.emit_expr(a) for a in node.args)
            return "Reflect.setField(" + obj + ", " + name + ", " + val + ")"
        if isinstance(node.func, ast.Name) and node.func.id == "getattr" \
                and len(node.args) in (2, 3) and not node.keywords:
            obj = self.emit_expr(node.args[0])
            name = self.emit_expr(node.args[1])
            field = "Reflect.field(" + obj + ", " + name + ")"
            if len(node.args) == 3:
                default = self.emit_expr(node.args[2])
                return ("(Reflect.hasField(" + obj + ", " + name + ") ? " +
                        field + " : " + default + ")")
            return field

        # list(iterable) -> a materialized Array. Python's list() turns an
        # iterator (e.g. a Map's keys()) into a concrete list; Haxe has no
        # `list` type-call, so we comprehend the iterable into an Array.
        # list() with no args -> a fresh empty Array.
        if isinstance(node.func, ast.Name) and node.func.id == "list":
            if not node.args:
                return "[]"
            if len(node.args) == 1:
                tmp = self._fresh_tmp()
                return ("[for (" + tmp + " in " +
                        self._emit_iterable(node.args[0]) + ") " + tmp + "]")

        # Module-qualified constructor: `mod.ClassName(args)` where `mod` is
        # an imported module and `ClassName` is a known (scanned) class.
        # Emit `new Mod.ClassName(args)` — the cross-module `new` form Haxe
        # needs; otherwise it reads as calling the class as a function.
        if isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in self.imported_modules \
                and node.func.attr in self.classes \
                and node.func.attr[:1].isupper():
            cls = self.classes[node.func.attr]
            qualified = (self.imported_modules[node.func.value.id] + "." +
                         node.func.attr)
            init = cls["method_signatures"].get("__init__")
            if init is not None:
                resolved = self._resolve_to_positional(init, node)
                return "new " + qualified + "(" + ", ".join(resolved) + ")"
            args = [self.emit_expr(a) for a in node.args]
            return "new " + qualified + "(" + ", ".join(args) + ")"

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

        # set() / set(iterable) -> a Map<T, Bool> (the model for sets).
        # The empty form is a fresh Map; an iterable arg seeds it. Only the
        # zero-arg form appears in the target code; the seeded form builds
        # via a fold for completeness.
        if isinstance(node.func, ast.Name) and node.func.id == "set":
            if not node.args:
                return "new Map()"
            coll = self._emit_iterable(node.args[0])
            tmp = self._fresh_tmp()
            elem = self._fresh_tmp()
            return ("{ var " + tmp + " = new Map(); for (" + elem + " in " +
                    coll + ") " + tmp + ".set(" + elem + ", true); " +
                    tmp + "; }")

        # set.add(x) -> map.set(x, true): a set membership is a key with a
        # truthy value in the Map model.
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add" \
                and len(node.args) == 1:
            recv_kind = self._static_kind(node.func.value)
            if recv_kind is not None and recv_kind[0] == "set":
                receiver = self.emit_expr(node.func.value)
                return receiver + ".set(" + self.emit_expr(node.args[0]) + ", true)"

        # max(...) / min(...). Two+ scalar args -> nested Math.max/min;
        # a single iterable arg -> a fold over the collection. Python's
        # max/min are variadic-or-iterable; disciplined code uses the
        # two-arg scalar form or the single-list form.
        if isinstance(node.func, ast.Name) and node.func.id in ("max", "min") \
                and not node.keywords:
            fn = "Math." + node.func.id
            if len(node.args) >= 2:
                args = [self.emit_expr(a) for a in node.args]
                expr = args[0]
                for a in args[1:]:
                    expr = fn + "(" + expr + ", " + a + ")"
                return expr
            if len(node.args) == 1:
                # Reduce over an iterable: fold, keeping the running
                # extremum. `>` for max, `<` for min.
                coll = self.emit_expr(node.args[0])
                cmp = ">" if node.func.id == "max" else "<"
                return ("Lambda.fold(" + coll +
                        ", function(x, acc) return x " + cmp +
                        " acc ? x : acc, " + coll + "[0])")

        # sorted(iterable, key=fn) -> a copied array sorted by a comparator
        # derived from the key function. sorted(iterable) -> copy + default
        # sort. Python's sorted returns a new list; Haxe Array.sort mutates,
        # so we copy first.
        if isinstance(node.func, ast.Name) and node.func.id == "sorted" \
                and len(node.args) == 1:
            return self._emit_sorted(node)

        # dict.get(key) / dict.get(key, default). Haxe Map.get returns
        # Null<V> already (so the no-default form is a direct rename), but
        # the two-arg form needs an exists-guarded expression to supply the
        # Python default.
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            recv_kind = self._static_kind(node.func.value)
            if recv_kind is not None and recv_kind[0] == "map":
                receiver = self.emit_expr(node.func.value)
                if len(node.args) == 1:
                    return receiver + ".get(" + self.emit_expr(node.args[0]) + ")"
                if len(node.args) == 2:
                    key = self.emit_expr(node.args[0])
                    default = self.emit_expr(node.args[1])
                    return ("(" + receiver + ".exists(" + key + ") ? " +
                            receiver + ".get(" + key + ") : " + default + ")")

        # Map.values() / .keys() used as a standalone iterable expression
        # (e.g. passed to a comprehension or list()). In a for-loop header
        # these are handled by _format_for_iter; here we map them so they
        # compose in expression position.
        if isinstance(node.func, ast.Attribute) and not node.args \
                and node.func.attr in ("values", "keys"):
            recv_kind = self._static_kind(node.func.value)
            if recv_kind is not None and recv_kind[0] == "map":
                receiver = self.emit_expr(node.func.value)
                if node.func.attr == "values":
                    # Iterating a Haxe Map yields its values.
                    return receiver
                return receiver + ".keys()"

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
        args = [self._emit_arg(a) for a in node.args]
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
            if self.current_class_name != self.module_class_name:
                return self.module_class_name + "." + func_node.id
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

    def _emit_arg(self, arg):
        # Emit a call argument, ascribing a type to an empty `set()`/`dict()`/
        # `{}` literal so Haxe can resolve the multi-type Map abstract (a bare
        # `new Map()` in argument position has no inference context).
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) \
                and arg.func.id == "set" and not arg.args:
            # A set is modeled as Map<T, Bool>; default key String (Haxe Maps
            # cannot key on Dynamic).
            return "(new Map() : Map<String, Bool>)"
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) \
                and arg.func.id == "dict" and not arg.args:
            return "(new Map() : Map<Dynamic, Dynamic>)"
        if isinstance(arg, ast.Dict) and not arg.keys:
            return "(new Map() : Map<Dynamic, Dynamic>)"
        return self.emit_expr(arg)

    def _looks_like_class_call(self, func_node):
        # Match a bare Name whose identifier is a class name: capitalized
        # (Counter()) or a private/local class following the leading-
        # underscore convention with a capitalized stem (_BackEdge() — F4a).
        # Such a name emits as a Haxe class, so the call needs `new`.
        if isinstance(func_node, ast.Name):
            name = func_node.id
            stem = name.lstrip("_")
            return len(stem) > 0 and stem[0].isupper()
        # Don't treat Foo.bar() as construction even if Foo is a class —
        # that's a static method call, which is just `Foo.bar()` in Haxe.
        return False

    def expr_Attribute(self, node):
        # Cross-module qualified reference (Milestone 8): `module.func` /
        # `module.CONST` where `module` was brought in via `import module`.
        # Resolve to the capitalized Haxe module class: `ConstraintEval.func`.
        # Done before emitting the receiver so the lowercase module name
        # (an "unknown identifier" in Haxe) never reaches the output.
        if isinstance(node.value, ast.Name) and node.value.id in self.imported_modules:
            return self.imported_modules[node.value.id] + "." + node.attr
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
        # A class-variable (Haxe static) accessed via `self.NAME`: Haxe forbids
        # instance access to statics, so route through `ClassName.NAME`.
        if receiver == "this" and self.current_class_name is not None \
                and node.attr in self.class_statics.get(self.current_class_name, set()):
            return self.current_class_name + "." + attr
        # Static access on a known class (e.g. `ZIndexEngine._flatten`):
        # private members are declared with their underscores stripped, so
        # the call site must strip too. Guard on the stripped name actually
        # being a member of that class so external `Foo._bar` is untouched.
        elif isinstance(node.value, ast.Name) and node.value.id in self.classes \
                and self._is_private_name(attr):
            if self._class_has_member(node.value.id, attr):
                attr = self._strip_private_underscores(attr)
        # Tagged-union downcast: disciplined Python models variants as a
        # base class with a `kind` discriminator and per-variant subclasses
        # carrying the extra fields, accessed after checking `kind`. Haxe's
        # nominal typing rejects `base.subfield`. When the receiver is a
        # variable of a known base class that does NOT declare this attr but
        # a subclass does, route the access through an untyped cast so it
        # resolves at runtime — matching the Python duck-typed access.
        recv_info = self._static_kind(node.value)
        if recv_info is not None and recv_info[0] == "class":
            cls_name = recv_info[1]
            if not self._class_has_member(cls_name, attr) \
                    and self._subclass_has_member(cls_name, attr):
                return "(cast " + receiver + ")." + attr
        return receiver + "." + attr

    def expr_List(self, node):
        elements = [self.emit_expr(e) for e in node.elts]
        literal = "[" + ", ".join(elements) + "]"
        # Heterogeneous literal (e.g. a guide spec `[x, y, x, y, "#00FFFF"]`
        # mixing Floats and a String): Haxe rejects mixed element types unless
        # the array is explicitly Array<Dynamic>. Detect a literal mixing
        # string and numeric constants and force the type.
        if self._is_heterogeneous_literal(node):
            return "([" + ", ".join(elements) + "] : Array<Dynamic>)"
        return literal

    def _is_heterogeneous_literal(self, node):
        # True if the list literal mixes string and numeric constant elements.
        has_str = False
        has_num = False
        for e in node.elts:
            if isinstance(e, ast.Constant):
                if isinstance(e.value, str):
                    has_str = True
                elif isinstance(e.value, (int, float)) and not isinstance(e.value, bool):
                    has_num = True
            elif isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.USub) \
                    and isinstance(e.operand, ast.Constant):
                has_num = True
        return has_str and has_num

    def _emit_iterable(self, node):
        # Emit an expression for use as a Haxe `for (x in <here>)` source.
        # `range(...)` -> a..b; `dict.keys()`/`dict.values()` map to the
        # right Haxe Map iteration; a bare Map name iterates its VALUES in
        # Haxe but its KEYS in Python, so a Map used directly as an iterable
        # (Python key iteration) becomes `.keys()`.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "range":
            return self._format_for_iter(node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and not node.args and node.func.attr in ("values", "keys"):
            recv_kind = self._static_kind(node.func.value)
            if recv_kind is not None and recv_kind[0] == "map":
                receiver = self.emit_expr(node.func.value)
                # Haxe Map iterates values directly; keys() for the key set.
                return receiver if node.func.attr == "values" else receiver + ".keys()"
        # A Map/set iterated directly: Python yields keys -> Haxe .keys().
        kind = self._static_kind(node)
        if kind is not None and kind[0] in ("map", "set"):
            return self.emit_expr(node) + ".keys()"
        return self.emit_expr(node)

    def expr_ListComp(self, node):
        # `[elt for x in it (if cond)...]` -> Haxe array comprehension
        # `[for (x in it) (if (cond)) elt]`. Disciplined code keeps this
        # to a single generator; nested generators / multiple `if`s are
        # joined left-to-right where present.
        return self._emit_comprehension(node, node.elt, None)

    def expr_DictComp(self, node):
        # `{k: v for x in it (if c)}` has no Haxe literal form. Build a Map
        # in an inline block expression: { var m = new Map(); for ... ; m; }
        return self._emit_comprehension(node, node.value, node.key)

    def expr_SetComp(self, node):
        # Sets are modeled as Map<T, Bool>; build one like a DictComp whose
        # value is always `true`.
        true_node = ast.copy_location(ast.Constant(value=True), node)
        return self._emit_comprehension(node, true_node, node.elt)

    def _comp_gen_header(self, gen):
        # Haxe `for (...)` header for one comprehension generator. Handles
        # `k, v in m.items()` -> `k => v in m`; otherwise `target in iterable`.
        kv = self._items_kv_target(gen.target, gen.iter)
        if kv is not None:
            return kv
        return self.emit_expr(gen.target) + " in " + self._emit_iterable(gen.iter)

    def _emit_comprehension(self, node, value_elt, key_elt):
        # Shared engine. key_elt is None for list comprehensions (array
        # result) and set when building a Map (DictComp/SetComp). Generators
        # and their `if` filters are nested in order.
        # Build the innermost emission, then wrap each generator outward.
        # We emit Haxe comprehension syntax for the array case and an
        # inline map-building block for the map case.
        gens = node.generators
        if key_elt is None:
            # Array comprehension. Compose: [for (g0) if(c..) for(g1)... elt]
            parts = []
            for gen in gens:
                parts.append("for (" + self._comp_gen_header(gen) + ") ")
                for cond in gen.ifs:
                    parts.append("if (" + self._emit_test(cond) + ") ")
            elt = self.emit_expr(value_elt)
            return "[" + "".join(parts) + elt + "]"
        # Map-building inline block expression.
        m = self._fresh_tmp()
        chunks = ["{ var " + m + " = new Map(); "]
        for gen in gens:
            chunks.append("for (" + self._comp_gen_header(gen) + ") ")
            for cond in gen.ifs:
                chunks.append("if (" + self._emit_test(cond) + ") ")
        chunks.append(m + ".set(" + self.emit_expr(key_elt) + ", " +
                      self.emit_expr(value_elt) + "); ")
        chunks.append(m + "; }")
        return "".join(chunks)

    def expr_IfExp(self, node):
        # Python conditional expression `a if cond else b` -> Haxe ternary.
        test = self._emit_test(node.test)
        body = self.emit_expr(node.body)
        orelse = self.emit_expr(node.orelse)
        return "(" + test + " ? " + body + " : " + orelse + ")"

    def _emit_sorted(self, node):
        # sorted(iterable) / sorted(iterable, key=fn). Returns a NEW array
        # (Python semantics), so copy before the in-place Haxe Array.sort.
        # With a key function, derive a comparator that compares key(a) to
        # key(b); without one, compare elements with Reflect.compare.
        coll = self.emit_expr(node.args[0])
        key_fn = None
        for kw in node.keywords:
            if kw.arg == "key":
                key_fn = self.emit_expr(kw.value)
        copied = coll + ".copy()"
        a, b = self._fresh_tmp(), self._fresh_tmp()
        if key_fn is not None:
            cmp = ("function(" + a + ", " + b + ") return Reflect.compare(" +
                   key_fn + "(" + a + "), " + key_fn + "(" + b + "))")
        else:
            cmp = ("function(" + a + ", " + b + ") return Reflect.compare(" +
                   a + ", " + b + ")")
        # `{ var t = coll.copy(); t.sort(cmp); t; }` as a block expression
        # so the sorted copy is the value.
        t = self._fresh_tmp()
        return ("{ var " + t + " = " + copied + "; " + t + ".sort(" + cmp +
                "); " + t + "; }")

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

def convert(source, filename="<input>", shared_tuples=False):
    tree = ast.parse(source, filename=filename)
    emitter = HaxeEmitter()
    emitter.shared_tuples = shared_tuples
    # Derive the module class name from the source filename (Milestone 8).
    # `<input>` / no real path keeps the historical "Main" default so
    # single-snippet conversions are unchanged.
    import os
    base = os.path.basename(filename)
    if base and base not in ("<input>", "<string>") and base.endswith(".py"):
        emitter.module_class_name = emitter._module_to_class_name(base[:-3])
        # Remember the source directory so emit_module can scan sibling
        # modules for cross-module type info (Milestone 8 type-loading).
        emitter._source_dir = os.path.dirname(os.path.abspath(filename))
    emitter._comments = _extract_comments(source)
    emitter.emit_module(tree)
    emitter._drain_remaining_comments()
    return emitter.output()


def emit_tuples_module(arities):
    # Produce a standalone `Tuples.hx` defining TupleN for each given arity —
    # the shared home used with `convert(..., shared_tuples=True)`.
    return HaxeEmitter().emit_tuples_module(arities)


def emit_runtime_module():
    # Produce the standalone `Pyhaxe.hx` runtime-support module (F8 truthy()).
    return HaxeEmitter().emit_runtime_module()


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
    args = sys.argv[1:]
    shared_tuples = False
    if "--shared-tuples" in args:
        shared_tuples = True
        args = [a for a in args if a != "--shared-tuples"]
    # `--emit-tuples A B ...` writes a standalone Tuples.hx for those arities.
    if args and args[0] == "--emit-tuples":
        arities = [int(a) for a in args[1:]] or [2, 3, 4]
        print(emit_tuples_module(arities))
        return 0
    # `--emit-runtime` writes the standalone Pyhaxe.hx runtime-support module.
    if args and args[0] == "--emit-runtime":
        print(emit_runtime_module())
        return 0
    if len(args) != 1:
        print("usage: haxe_emitter.py [--shared-tuples] FILE.py", file=sys.stderr)
        print("       haxe_emitter.py --emit-tuples N [N...]", file=sys.stderr)
        print("       haxe_emitter.py --emit-runtime", file=sys.stderr)
        return 1
    f = open(args[0], "r")
    try:
        source = f.read()
    finally:
        f.close()
    print(convert(source, args[0], shared_tuples=shared_tuples))
    return 0


if __name__ == "__main__":
    sys.exit(main())
