"""
types_example.py

Milestone 6 demonstration program. Exercises the new type-system
features and tuple support:
    - X | None and Optional[X] both map to Null<X>
    - X | Y and Union[X, Y] both map to EitherType<X, Y>
    - Any maps to Dynamic
    - Callable[[A, B], R] maps to (A, B) -> R
    - tuple[A, B] maps to auto-generated Tuple2<A, B>
    - tuple literal (a, b) maps to new Tuple2(a, b)
    - t[0] (literal index) on tuple-typed var maps to t._0
    - t[i] (variable index) maps to t.at(i)
"""

from typing import List, Optional, Union, Any, Callable


def first_or_none(items: List[int]) -> int | None:
    if len(items) == 0:
        return None
    return items[0]


def maybe_double(x: Optional[int]) -> Optional[int]:
    if x is None:
        return None
    return x * 2


def stringify(value: int | str) -> str:
    return str(value)


def label(value: Union[int, float, str]) -> str:
    return str(value)


def passthrough(value: Any) -> Any:
    return value


def apply_binop(op: Callable[[int, int], int], a: int, b: int) -> int:
    return op(a, b)


def stats(values: List[float]) -> tuple[float, int]:
    total: float = 0.0
    for v in values:
        total += v
    return (total, len(values))


def lookup(table: List[tuple[str, int]], key: str) -> int:
    i: int = 0
    while i < len(table):
        entry: tuple[str, int] = table[i]
        if entry[0] == key:
            return entry[1]
        i += 1
    return -1


def use_tuples() -> int:
    result: tuple[float, int] = stats([1.0, 2.0, 3.0])
    total: float = result[0]
    count: int = result[1]
    return count


def variable_index(t: tuple[int, int, int], i: int) -> int:
    return t[i]
