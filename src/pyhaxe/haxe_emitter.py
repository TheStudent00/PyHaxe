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

Future milestones (in order):
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
    # Python's built-in Exception maps to Haxe's recommended base class.
    "Exception": "haxe.Exception",
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
        # Emit the body into a separate list so we can prepend any
        # auto-generated TupleN classes once we know which arities were
        # actually used.
        body_lines = []
        saved = self.lines
        self.lines = body_lines
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.lines = saved
        # Now emit TupleN classes (if any), then the body.
        self._emit_tuple_classes()
        self.lines.extend(body_lines)

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
        return {"params": params, "uses_options": has_defaults}

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
        signature = self.functions.get(node.name)
        if signature is not None and signature["uses_options"]:
            self._emit_options_function(node, signature, type_name=self._options_typename(node.name))
            return

        params = self._format_params(node.args)
        ret = self.emit_type(node.returns)
        self.line("function " + node.name + "(" + params + "):" + ret + " {")
        self.indent_level += 1
        prev_var_types = dict(self.var_types)
        self._register_param_var_types(node.args)
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.var_types = prev_var_types
        self.indent_level -= 1
        self.line("}")
        self.line("")

    def _register_param_var_types(self, args):
        # Add tuple-typed parameters to var_types so subscript access on
        # them rewrites correctly inside the function body.
        for arg in args.args:
            if arg.arg in ("self", "cls"):
                continue
            arity = self._tuple_arity_of(arg.annotation)
            if arity is not None:
                self.var_types[arg.arg] = ("tuple", arity)

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
        self._emit_options_prelude(signature)
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
        # Track tuple-typed locals/fields so expr_Subscript can rewrite
        # `t[0]` -> `t._0` when the index is a literal int.
        if isinstance(node.target, ast.Name):
            tuple_arity = self._tuple_arity_of(node.annotation)
            if tuple_arity is not None:
                self.var_types[node.target.id] = ("tuple", tuple_arity)
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

    def stmt_For(self, node):
        # Disciplined Python: target is always a single Name (no tuple
        # unpacking), iter is either a `range(...)` call or a collection.
        target = self.emit_expr(node.target)
        iter_expr = self._format_for_iter(node.iter)
        self.line("for (" + target + " in " + iter_expr + ") {")
        self.indent_level += 1
        for stmt in node.body:
            self.emit_stmt(stmt)
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
        for stmt in node.body:
            self.emit_stmt(stmt)
        self.indent_level -= 1

        # Each handler opens with `} catch (...)` (closing the previous
        # block, opening its own); the very last one closes with `}`.
        for handler in node.handlers:
            self._emit_except_handler_open(handler)
            self.indent_level += 1
            for stmt in handler.body:
                self.emit_stmt(stmt)
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
            func = self.emit_expr(node.func)
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
        func = self.emit_expr(node.func)
        args = [self.emit_expr(a) for a in node.args]
        for kw in node.keywords:
            args.append("/*kwarg " + kw.arg + "=*/" + self.emit_expr(kw.value))

        if self._looks_like_class_call(node.func):
            return "new " + func + "(" + ", ".join(args) + ")"
        return func + "(" + ", ".join(args) + ")"

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

        # Tuple indexing: `t[0]` -> `t._0` when t is tuple-typed and the
        # index is a literal int (the common case, fully typed). For
        # variable indices on tuples, fall back to `t.at(i)` which
        # returns Dynamic.
        if isinstance(node.value, ast.Name):
            var_info = self.var_types.get(node.value.id)
            if var_info is not None and var_info[0] == "tuple":
                receiver = self.emit_expr(node.value)
                if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, int):
                    return receiver + "._" + str(slice_node.value)
                return receiver + ".at(" + self.emit_expr(slice_node) + ")"

        receiver = self.emit_expr(node.value)
        index = self.emit_expr(slice_node)
        return receiver + "[" + index + "]"

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
