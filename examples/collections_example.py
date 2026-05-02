"""
collections_example.py

Milestone 3 demonstration program. Exercises the collection and
iteration features the emitter handles:
    - list literals and indexing
    - dict literals
    - for x in collection
    - for i in range(N)
    - len(x) -> x.length
    - list.append(x) -> list.push(x)
    - subscript read and write
"""

from typing import List, Dict


class TodoList:
    items: List[str]
    priorities: Dict[str, int]

    def __init__(self):
        self.items = []
        self.priorities = {}

    def add(self, item: str, priority: int) -> None:
        self.items.append(item)
        self.priorities[item] = priority

    def count(self) -> int:
        return len(self.items)

    def get(self, index: int) -> str:
        return self.items[index]

    def replace(self, index: int, item: str) -> None:
        self.items[index] = item

    def total_priority(self) -> int:
        total: int = 0
        for item in self.items:
            total += self.priorities[item]
        return total

    def first_n_summary(self, n: int) -> str:
        result: str = ""
        for i in range(n):
            if i >= len(self.items):
                break
            if i > 0:
                result += ", "
            result += self.items[i]
        return result


def make_squares(n: int) -> List[int]:
    result: List[int] = []
    for i in range(n):
        result.append(i * i)
    return result


def sum_range(start: int, stop: int) -> int:
    total: int = 0
    for i in range(start, stop):
        total += i
    return total
