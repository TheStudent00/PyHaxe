"""
exceptions_example.py

Milestone 5 demonstration program. Exercises try/except/raise.
"""


class ValidationError(Exception):
    pass


class StorageError(Exception):
    pass


def parse_positive_int(value: int) -> int:
    if value <= 0:
        raise ValidationError("not a positive integer")
    return value


class Repository:
    items: List[str]

    def __init__(self):
        self.items = []

    def add(self, item: str) -> None:
        if item == "":
            raise ValidationError("empty item")
        self.items.append(item)

    def get(self, index: int) -> str:
        if index < 0:
            raise StorageError("negative index")
        if index >= len(self.items):
            raise StorageError("index out of range")
        return self.items[index]

    def safe_get(self, index: int) -> str:
        try:
            return self.get(index)
        except StorageError as e:
            return "<missing>"

    def safe_add(self, item: str) -> bool:
        try:
            self.add(item)
            return True
        except ValidationError as e:
            return False
        except Exception as e:
            return False


def find_or_default(repo: Repository, index: int, default: str) -> str:
    try:
        return repo.get(index)
    except Exception as e:
        return default


def validate_and_process(items: List[str]) -> int:
    successful: int = 0
    for item in items:
        try:
            if item == "fail":
                raise ValidationError("explicit fail")
            successful += 1
        except ValidationError as e:
            # log and continue
            successful += 0
    return successful
