"""
bad_example.py

Deliberately violates the discipline. Used to demonstrate the linter.
Each violation is labeled with the kind the checker should report.
"""

from typing import List


class FirstBase:
    pass


class SecondBase:
    pass


# Violation: multiple-inheritance
class BadClass(FirstBase, SecondBase):

    def __init__(self) -> None:
        self.x = 0
        self.y = 0

    # Violation: missing-param-annotation, missing-return-annotation
    def add(self, a, b):
        return a + b

    def process(self, items: List[int]) -> int:
        result: int = 0

        # Violation: with-statement
        with open("temp.txt") as f:
            data: str = f.read()

        # Violation: tuple-unpacking
        a, b = 1, 2

        # Violation: lambda
        squared = lambda x: x * x

        # Violation: generator-expression
        gen = (i for i in items)

        return result

    # Violation: varargs, kwargs-param
    def flexible(self, *args, **kwargs) -> None:
        pass

    # Violation: yield (makes this a generator)
    def stream(self) -> int:
        yield 1
        yield 2
