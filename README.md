*note: early days*

# disciplined-haxe

A linter and transpiler that lets you write Python and ship Haxe.

The toolchain has two pieces. The **discipline checker** lints Python code against a subset designed for cross-language portability. The **Haxe emitter** compiles that disciplined Python to readable Haxe source. Once your code follows the discipline, translation is mechanical.

## Why this exists

Python's syntax is among the most refined available, but writing Python locks you into the Python runtime. Every other major language (Haxe, Rust, Java, JavaScript, C++) requires you to commit to its syntax, idioms, and tooling. Switching languages means rewriting.

This project takes a different approach. Define a Python subset that maps cleanly onto major target languages, provide tools to enforce the subset, and emit target-language source mechanically. You write Python. You debug in Python. You ship Haxe — and through Haxe, anything Haxe targets (JavaScript, Java, C++, C#, PHP, Lua, and others).

The cost is discipline: type annotations everywhere, no lambdas, no `with`, no tuple unpacking, wrappers for non-portable APIs. The benefit is one source of truth that runs anywhere.

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
function classify(value:Int):String {
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

Early development. Milestone 1 of the emitter is verified working on a small example program. The discipline checker is functional and tested against deliberate-violation examples.

| Component | Status |
|-----------|--------|
| Discipline checker (linter) | complete |
| Emitter — Milestone 1: functions, expressions, control flow | complete |
| Emitter — Milestone 2: classes, fields, methods, inheritance | planned |
| Emitter — Milestone 3: collections and iteration | planned |
| Emitter — Milestone 4: wrapper handling (`@haxe_extern`) | planned |
| Emitter — Milestone 5: signature-aware kwargs resolution | planned |
| Emitter — Milestone 6: try/except, raise | planned |
| Emitter — Milestone 7: extended type system (Optional, List, Dict) | planned |
| Emitter — Milestone 8: imports and module organization | planned |
| Polish — comment preservation, formatting, error reporting | planned |

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the full milestone breakdown.

## Installation

```bash
git clone https://github.com/<user>/disciplined-haxe
cd disciplined-haxe
pip install -e .
```

This installs the package in development mode and provides two CLI commands: `dh-check` and `dh-emit`.

## Usage

Lint a Python file against the discipline:

```
$ dh-check examples/inventory_example.py
examples/inventory_example.py: ok

$ dh-check examples/bad_example.py
examples/bad_example.py: 12 violation(s)
  line 20: [multiple-inheritance] class BadClass inherits from multiple classes; only one allowed
  line 27: [missing-return-annotation] function add has no return type annotation
  line 34: [with-statement] with statements have no Haxe equivalent; use try/finally pattern
  line 38: [tuple-unpacking] tuple unpacking not allowed; assign individual variables
  ...
```

The checker exits non-zero when violations are found, so it drops into CI without ceremony.

Compile a disciplined Python file to Haxe:

```
$ dh-emit examples/basic_example.py > out/Basic.hx
$ haxe -main Basic -js basic.js
```

## The discipline (brief)

The full discipline is documented in [`docs/DISCIPLINE.md`](docs/DISCIPLINE.md). In short:

**Required.** Type annotations on every parameter and return value. Class-level field declarations with type annotations. Single inheritance only. Named functions instead of lambdas. Broad exception catches.

**Forbidden.** `*args` and `**kwargs` in signatures (though kwargs at call sites are fine — the converter resolves them positionally). Tuple unpacking. `with` statements. Generators and `yield`. Multiple inheritance.

**Wrapper required.** Non-portable libraries — numpy, file I/O, regex, threading, anything platform-specific — are accessed through wrapper classes marked with the `@haxe_extern` decorator. The decorator tells the converter to skip the class body and assume a hand-written target equivalent exists.

**Allowed because Haxe handles it well.** `for x in collection`, `for i in range(N)`, `len(x)`, string concatenation with `+`, simple list comprehensions. Disciplines that would be required for more conservative targets are relaxed when Haxe specifically supports them.

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

Each AST node type gets a `stmt_X` or `expr_X` method. Unknown node types emit a `// TODO` comment rather than crashing, so partial coverage is visible in output. This is what makes incremental development safe — you can run the converter at any milestone level on any input and immediately see which constructs aren't implemented yet.

## Project structure

```
src/disciplined_haxe/
    discipline.py        the @haxe_extern decorator
    checker.py           AST linter for the disciplined subset
    emitter.py           AST -> Haxe converter
    cli.py               dh-check and dh-emit entry points
examples/
    basic_example.py     Milestone 1 demo (functions, expressions, control flow)
    inventory_example.py larger example (classes, collections, exceptions)
    bad_example.py       deliberate violations for the checker
tests/
    test_checker.py
    test_emitter.py
docs/
    DESIGN.md            full design history and rationale
    DISCIPLINE.md        discipline rules as a reference
    ROADMAP.md           milestone breakdown
```

## Contributing

The project is in early stages. Contributions are especially welcome for working through the milestone roadmap in order, building wrapper classes for common libraries (the accumulated wrapper library is the long-term value of this project), and translating real-world Python codebases into the disciplined subset — every translation surfaces patterns the linter doesn't yet catch.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup and coding standards.

## Acknowledgements

Inspired by [Haxe](https://haxe.org), [CoffeeScript](https://coffeescript.org), and protobuf's wrapper-based approach to cross-language interop. Built on Python's standard `ast` module — which does most of the heavy lifting and asks nothing in return.

## License

MIT — see [`LICENSE`](LICENSE).
