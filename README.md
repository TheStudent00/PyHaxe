# PyHaxe

A linter and transpiler that lets you write Python and ship Haxe.

The toolchain has two pieces. The **discipline checker** lints Python code against a subset designed for cross-language portability. The **Haxe emitter** compiles that disciplined Python to readable Haxe source. Once your code follows the discipline, translation is mechanical.

## Why this exists

Python's syntax is among the most refined available, but writing Python locks you into the Python runtime. Every other major language (Haxe, Rust, Java, JavaScript, C++) requires you to commit to its syntax, idioms, and tooling. Switching languages means rewriting.

This project takes a different approach. Define a Python subset that maps cleanly onto major target languages, provide tools to enforce the subset, and emit target-language source mechanically. You write Python. You debug in Python. You ship Haxe — and through Haxe, anything Haxe targets (JavaScript, Java, C++, C#, PHP, Lua, and others).

The cost is discipline: type annotations everywhere, no lambdas, no `with`, wrappers for non-portable APIs. The benefit is one source of truth that runs anywhere.

## Quick example

This disciplined Python:

```python
def classify(value: int) -> str:
    if value > 0:
        return "positive"
    elif value < 0:
        return "negative"
    else:
        return "zero"
```

Compiles to this Haxe:

```haxe
public static function classify(value:Int):String {
    if (value > 0) {
        return "positive";
    } else if (value < 0) {
        return "negative";
    } else {
        return "zero";
    }
}
```

The transformation is mechanical — keyword and type renames, brace insertion, semicolon termination. No semantic guesswork, no AST analysis beyond what's already in Python's standard library.

## Status

The transpiler is functional and verified end-to-end. Real disciplined-Python programs translate to Haxe source that compiles with the official `haxe` compiler and produces correct output across Haxe's targets (verified on JavaScript via Node.js).

| Component | Status |
|-----------|--------|
| Discipline checker (linter) | complete |
| Functions, expressions, control flow | complete |
| Classes, fields, methods, inheritance | complete |
| Collections and iteration | complete |
| Visibility (public / private via underscore convention) | complete |
| Signature-aware kwargs resolution (auto-options-struct) | complete |
| Try / except / raise | complete |
| Type system (Optional, Union, Callable, Any, tuples) | complete |
| Modules and Main wrapper | complete |
| Polish — comments, formatting, precedence-aware parens | complete |

The roadmap from here is real-world use: building a wrapper library for common Python APIs (`PyHaxeSafe`), supporting cross-module imports, and translating real codebases.

## Installation

```bash
git clone https://github.com/TheStudent00/PyHaxe
cd PyHaxe
pip install -e .
```

This installs the package in development mode and provides two CLI commands: `pyhaxe-check` and `pyhaxe-emit`.

## Usage

Lint a Python file against the discipline:

```
$ pyhaxe-check examples/inventory_example.py
examples/inventory_example.py: ok

$ pyhaxe-check examples/bad_example.py
examples/bad_example.py: 15 violation(s)
  line 20: [multiple-inheritance] class BadClass inherits from multiple classes; only one allowed
  line 27: [missing-return-annotation] function add has no return type annotation
  line 34: [with-statement] with statements have no Haxe equivalent; use explicit acquire/release
  line 38: [tuple-unpacking] tuple unpacking not allowed; assign individual variables
  ...
```

The checker exits non-zero when violations are found, so it drops into CI without ceremony.

Compile a disciplined Python file to Haxe:

```
$ pyhaxe-emit examples/inventory_example.py > out/Inventory.hx
$ haxe -main Main -js inventory.js
```

The `inventory_example.py` translates to a multi-class Haxe file with no manual edits required: it includes a generated `Main` class wrapping the `if __name__ == "__main__":` block, options-struct typedefs for any constructors with default arguments, and an `extern class` stub for each `@haxe_extern`-marked wrapper.

## The discipline

**Required.** Type annotations on every parameter and return value. Class-level field declarations with type annotations. Single inheritance only. Named functions instead of lambdas. Broad exception catches (`except Exception`, not `except IndexError`).

**Forbidden.** `*args` and `**kwargs` in signatures (though kwargs at call sites are fine — the transpiler resolves them). Tuple unpacking on the left of `=`. `with` statements. Generators and `yield`. Multiple inheritance. `try / finally` and `try / else` (no Haxe equivalent). Bare `raise` (must use `raise e` with explicit name).

**Wrapper required.** Non-portable libraries — numpy, file I/O, regex, threading, anything platform-specific — are accessed through wrapper classes marked with the `@haxe_extern` decorator. The decorator tells the transpiler to emit a stub `extern class` declaration and skip the body, expecting a hand-written target-side implementation alongside the generated code.

**Allowed because Haxe handles it well.** `for x in collection`, `for i in range(N)`, `len(x)`, string concatenation with `+`, list/dict literals, list/dict subscripting, structural exception hierarchies, kwargs at call sites. Native Python tuples (`tuple[A, B]`, `(a, b)` literals) translate to auto-generated `TupleN` classes — only the arities you actually use get emitted.

## How it works

Both the checker and the emitter use Python's standard `ast` module to parse Python source into a tree of typed objects, then walk the tree with `ast.NodeVisitor`. No regex. No grammar files. No parsing code.

```python
class HaxeEmitter:
    def emit_stmt(self, node):
        method = "stmt_" + type(node).__name__
        handler = getattr(self, method, None)
        if handler is None:
            self.line("// TODO stmt: " + type(node).__name__)
            return
        handler(node)
```

Each AST node type gets a `stmt_X` or `expr_X` method. Unknown node types emit a `// TODO` comment rather than crashing, so partial coverage is visible in output. Comments from the source are preserved separately via Python's `tokenize` module (the `ast` module strips them) and re-injected at the right line positions during emission.

## Project structure

```
src/pyhaxe/
    discipline.py            the @haxe_extern decorator
    discipline_checker.py    AST linter for the disciplined subset
    haxe_emitter.py          AST -> Haxe converter
    cli.py                   pyhaxe-check and pyhaxe-emit entry points
examples/
    basic_example.py         functions, expressions, control flow
    classes_example.py       classes, inheritance, static methods
    collections_example.py   for-loops, lists, dicts, len, append
    visibility_example.py    private/public via underscore convention
    kwargs_example.py        options-struct for kwargs functions
    exceptions_example.py    try/except/raise
    types_example.py         Optional, Union, Callable, tuples
    inventory_example.py     larger example using most features
    bad_example.py           deliberate violations for the checker
tests/
    test_pyhaxe.py           lint and emitter regression tests
docs/
    DEVELOPMENT_NOTES.md     workarounds, gotchas, design decisions
```

## Contributing

The project is in active development. Contributions especially welcome for:
- building wrapper classes for common libraries (the accumulated wrapper library is the long-term value of this project)
- translating real-world Python codebases into the disciplined subset (every translation surfaces patterns the linter doesn't yet catch)
- supporting cross-module imports (currently typing imports are stripped, others are passed through as comments)

## Acknowledgements

Inspired by [Haxe](https://haxe.org), [CoffeeScript](https://coffeescript.org), and protobuf's wrapper-based approach to cross-language interop. Built on Python's standard `ast` module — which does most of the heavy lifting and asks nothing in return.

Development in collaboration with Claude (Opus 4.7 Adaptive. Accessed May 2026).

## License

OTU-GL — see [`LICENSE`](https://github.com/TheStudent00/PyHaxe/blob/main/OTU%20GREEN%20LICENSE%20FOR%20UNIVERSAL%20WORKS.pdf).
