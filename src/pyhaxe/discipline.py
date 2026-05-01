"""
discipline.py

Shared infrastructure for the universal-language project's Python side.

Currently provides:
    haxe_extern — a class decorator marking wrapper classes that should
                  be skipped during discipline checking and translation.
                  The wrapper's body is manually maintained per-target;
                  the converter assumes a hand-written Haxe equivalent.

Tooling code (this module, the discipline checker, the converter itself)
runs only in Python and is exempt from the discipline. Application code
that needs to translate to other targets follows the discipline.
"""

from typing import Callable, Optional, Type


def haxe_extern(haxe_name: Optional[str] = None) -> Callable[[Type], Type]:
    """Mark a class as a wrapper.

    The discipline checker skips the class body during linting.
    The Haxe converter does not emit a translation for the body and
    instead expects a hand-written Haxe class with the same external API.

    Usage:
        @haxe_extern()             # uses the Python class name
        class Numpy: ...

        @haxe_extern("MyNumpy")    # explicit target name
        class Numpy: ...
    """

    def decorator(cls: Type) -> Type:
        cls.__haxe_extern__ = haxe_name or cls.__name__
        return cls

    return decorator
