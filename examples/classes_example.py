"""
classes_example.py

Milestone 2 demonstration program. Exercises the class features the
emitter handles:
    - class with field declarations
    - constructor with default parameter values
    - methods, including overridden ones
    - single inheritance with super().__init__()
    - @staticmethod
    - @haxe_extern wrapper class (emits `extern class` stub)
"""

from pyhaxe.discipline import haxe_extern


@haxe_extern()
class Print:
    @staticmethod
    def line(message: str) -> None:
        print(message)


class Counter:
    value: int
    step: int

    def __init__(self, start: int = 0, step: int = 1):
        self.value = start
        self.step = step

    def increment(self) -> int:
        self.value += self.step
        return self.value

    def reset(self) -> None:
        self.value = 0

    @staticmethod
    def make_default() -> "Counter":
        return Counter()


class BoundedCounter(Counter):
    maximum: int

    def __init__(self, start: int = 0, step: int = 1, maximum: int = 100):
        super().__init__(start, step)
        self.maximum = maximum

    def increment(self) -> int:
        next_value: int = self.value + self.step
        if next_value > self.maximum:
            self.value = self.maximum
        else:
            self.value = next_value
        return self.value
