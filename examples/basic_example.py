"""
basic_example.py

Minimal disciplined-Python program demonstrating Milestone 1 of the
Haxe converter. Functions, basic expressions, control flow.

No classes, no collections, no kwargs — those come in later milestones.
"""


def add(a: int, b: int) -> int:
    result: int = a + b
    return result


def classify(value: int) -> str:
    if value > 0:
        return "positive"
    elif value < 0:
        return "negative"
    else:
        return "zero"


def repeat(text: str, count: int) -> str:
    result: str = ""
    i: int = 0
    while i < count:
        result = result + text
        i += 1
    return result


def safe_divide(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def in_range(value: int, low: int, high: int) -> bool:
    if value < low:
        return False
    if value > high:
        return False
    return True


def in_range_combined(value: int, low: int, high: int) -> bool:
    return value >= low and value <= high
