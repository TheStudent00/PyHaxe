"""
Command-line entry points for PyHaxe.

These are thin wrappers around the existing main() functions in
discipline_checker and haxe_emitter, exposed via pyproject.toml's
[project.scripts] table as `pyhaxe-check` and `pyhaxe-emit`.
"""

import sys

from pyhaxe.discipline_checker import main as _check_main
from pyhaxe.haxe_emitter import main as _emit_main


def check_main() -> None:
    sys.exit(_check_main())


def emit_main() -> None:
    sys.exit(_emit_main())
