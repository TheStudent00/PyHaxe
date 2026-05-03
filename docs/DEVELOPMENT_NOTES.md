# PyHaxe Development Notes

Internal-facing notes on workarounds, gotchas, design decisions, and known limitations encountered during development. The README and DESIGN doc cover the public-facing story; this file is for context that's useful to have around when the next change touches related code.


## Workarounds (real bugs found and fixed during development)

### Multi-catch brace chaining (Milestone 5)

**Problem.** First implementation of `stmt_Try` had each `_emit_except_handler` emit both its opener `} catch (...) {` and its closer `}`. With a single handler this works. With two or more handlers it produces a stray `}` between them — the first handler closes itself, then the second handler's opener emits another `}`, giving you `}\n} catch (...)`.

**Fix.** Restructured so handlers don't own their closing brace. `_emit_except_handler_open` only emits `} catch (...) {` and the body. `stmt_Try` emits the final standalone `}` once after all handlers are done.

**Lesson.** Bugs that only show up with N > 1 are easy to miss with single-instance test cases. The inventory example surfaced this; the original exceptions example wouldn't have.

### Method override detection (Milestone 2)

**Problem.** Haxe requires the `override` keyword on methods that override a parent's method. Without it the compiler errors with "Field X should be declared with 'override'." Python has no such keyword — methods just override silently.

**Fix.** Two-pass scan. The first pass (`_scan_classes`) walks every `ClassDef` and builds a registry: `{class_name: {bases, methods, method_signatures}}`. The second pass uses `_is_override(class_name, method_name)` which walks up the inheritance chain looking for a matching method name.

**Lesson.** Some Haxe rules can't be inferred locally — they need whole-module context. The class registry pattern works for this; we now reuse it for kwargs resolution and signature lookup.

### `in_class` flag pollution inside method bodies (Milestone 2)

**Problem.** The `in_class` flag was set true for the whole class body, including inside method bodies. Since `stmt_AnnAssign` checks `in_class` to decide between emitting `public var` (a field) vs plain `var` (a local), local variable declarations inside methods came out as `public var next_value:Int = ...`.

**Fix.** Push/pop `in_class` around method body emission in `_emit_method`. Save the flag, set it to False, emit the body, restore it.

**Lesson.** Context flags need explicit save/restore at every boundary that changes context. When for-loops or other scopes get added, similar care is needed if their inner statements have different emission rules.

### Field privacy enforced by Haxe but not Python

**Problem.** Initial code emitted `var name:Type` for class fields. Haxe defaults fields to private, so external access (`acc.balance`) failed at compile time with "Cannot access private field." Python doesn't have this concept — every attribute is public unless you use the leading-underscore convention.

**Fix.** Fields inside a class body emit as `public var name:Type` by default. Underscore-prefixed names emit as `private var` with the underscore stripped from the emitted name.

**Lesson.** Haxe enforces things Python only conventions. Translating a Python program to Haxe can surface "bugs" that were latent in the Python — code that worked because Python is permissive may fail to compile in Haxe.

### Multi-arg generic types (Milestone 3)

**Problem.** `Dict[str, int]` in Python AST is a `Subscript` whose `slice` is a `Tuple` of two type nodes. Initial `emit_type` only handled single-arg generics like `List[Item]`, so `Dict[str, int]` came out as `Map</* TODO type */>`.

**Fix.** Added a Tuple branch in `emit_type` that emits comma-separated inner types. Now correctly produces `Map<String, Int>`.

**Lesson.** Type AST is a small but real sublanguage. Always test type translation with the multi-arg case.

### Forward-reference type strings (Milestone 2)

**Problem.** Python lets you write `def make_default() -> "Counter":` (string forward reference) for types not yet defined when the function is defined. The AST gives this as a `Constant` with a string value, not a `Name`. Initial `emit_type` returned `/* TODO type */`.

**Fix.** Added a Constant-with-string-value branch that runs the string through `PYTHON_TO_HAXE_TYPES` and emits the result.

**Lesson.** Python type annotations are surprisingly varied at the AST level — they can be Name, Subscript, Constant, Tuple, BinOp (for `X | Y` union syntax), and more. Each new pattern surfaces from real code.


## Heuristics that could fail

### Class call detection by capitalization

`_looks_like_class_call` returns true for any `Name` starting with a capital letter, which causes the emitter to add `new` for class instantiation. This works because of PEP 8, but a `Name` like `SECRET_KEY` (uppercase constant) used as a callable would mistakenly get `new` prepended. The signature lookup in `_lookup_signature` papers over this in practice — when the registry resolves the call, it knows whether the target is a constructor or a function. Only the unresolved fallback path uses the capitalization heuristic.

If this becomes a problem, the fix is to require `_looks_like_class_call` to also confirm the name is in the class registry. That works for in-module classes but not for imported ones (until Milestone 7 brings cross-module info).

### `Optional[X]` doesn't enforce null checks at use sites

The emitter translates `Optional[Item]` to `Null<Item>` correctly, but doesn't insert null checks when the value is dereferenced. So `found.describe()` after `found: Optional[Item] = ...` will emit cleanly but null-deref at runtime if `found` is null. The disciplined-Python pattern of explicit `if found is not None:` checks fixes this — and the emitter handles `is`/`is not` against `None` correctly via the COMPARE_MAP additions.

If we ever want to be stricter, the linter could flag attribute access on Optional-typed locals not preceded by a None check. That requires lightweight type tracking through annotated assignments.


## Known structural limitations

### `if __name__ == "__main__":` translates literally

Currently translates to `if (__name__ == "__main__") { ... }` at module scope in Haxe — which is broken because Haxe doesn't allow statements outside a class. This is the biggest practical gap right now: it prevents the inventory example from compiling end-to-end without manual editing.

**Fix path.** Milestone 7 detects this idiom and wraps its body in a `static function main()` of an auto-generated `Main` class.

### `super().method()` for non-init doesn't fully work

`_format_super_init_call` is hardcoded to detect `super().__init__(...)`. Other forms like `super().some_method()` fall through to the unresolved path and emit `super().some_method()` literally — which Haxe accepts in some forms but not all (Haxe uses `super.some_method()` without parens on the super call).

**Fix path.** Generalize the super-detection to handle any `super().X(...)` and emit `super.X(...)`.

### Comments are stripped

Python's `ast` module discards comments entirely during parse. Preserving them requires using the `tokenize` module alongside `ast` and re-injecting comments at emission time based on line numbers.

**Fix path.** Milestone 8 polish.

### Class registry is module-local

If you `from other_module import OtherClass` and try to construct it with kwargs, the registry has no entry for `OtherClass`. The kwargs call falls through to the comment-fallback path.

**Fix path.** Milestone 7 (modules/imports) extends the registry to be cross-file by parsing imported modules for their exported types.

### Trailing blank lines inside classes

Each method emits a blank line after its closing `}` for top-level spacing. Inside a class body, this leaves a hanging blank before the class's closing `}`. Cosmetic only, no semantic effect.

**Fix path.** Milestone 8 polish.

### Parens on every BinOp

`a + b` becomes `(a + b)` because every binop wraps itself defensively. Correct but noisy. A precedence-aware emitter that tracks the parent operator's precedence would only insert parens when actually needed.

**Fix path.** Milestone 8 polish.


## Design decisions worth remembering

### Why detect kwargs from the function definition, not call sites

We considered triggering options-struct emission based on whether *any* call site uses kwargs. We settled on the function definition itself: if it has any default value, emit options-struct; otherwise emit positional. The decision is local (each function judged on its own definition), predictable for the programmer, and matches the actual benefit of kwargs at the call site (which only exist to skip defaults).

### Why broad exception catches only

Python's exception hierarchy includes specific types like `IndexError`, `KeyError`, `ValueError`, etc. that don't have direct Haxe equivalents. The discipline catches everything as `Exception` (or a user-defined custom subclass). If finer-grained catching is needed, the user defines their own exception hierarchy via `class FooError(Exception): pass`.

### Why method renames are limited to a small table

Currently only `append → push` is renamed. Larger Python-to-Haxe method mapping tables (like `extend → concat`, `pop → pop`, etc.) get tempting but each rename is a target-specific assumption. Keeping the table small means the discipline mostly tells the user "use the Haxe name" — which keeps the wrapper boundary explicit.

### Why @haxe_extern emits stub signatures, not bodies

Wrapper classes have target-specific implementations that the user maintains by hand. The emitter emits `extern class Name { signatures only }` so Haxe knows the type exists with the given API. The actual implementation file (e.g., `Print.hx` for the Haxe target) is the user's responsibility.


## On the Haxe AST API

Haxe has a full AST API exposed through `haxe.macro.Expr` with an `ExprDef` enum that mirrors Python's `ast` node types nearly one-to-one (constants, binary ops, calls, fields, blocks, ifs, fors, whiles, try-expressions, throws, returns, breaks, etc.). There's also an `ExprTools` utility class with `iter`, `map`, and `toString` analogous to Python's `NodeVisitor` / `NodeTransformer`.

A reorganization of PyHaxe along the lines of "Python AST → Haxe AST → Haxe code" is theoretically possible but would require porting `ExprTools.toString` to Python (since the Haxe printer is itself Haxe code, only available at macro time inside the Haxe compiler). Net effect is roughly 2-3x more code for the same functional output, and you lose the ability to inject things like TODO comments cleanly because they don't fit the AST schema. The current source-AST-to-text approach is the standard route taken by most production transpilers.
