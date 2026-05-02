"""
visibility_example.py

Demonstrates private/public visibility via Python's leading-underscore
convention. Both single-underscore (_foo) and double-underscore (__foo)
emit as `private` in Haxe; the underscores are stripped from the
emitted name.
"""


class BankAccount:
    holder: str
    _balance: float
    __pin: int

    def __init__(self, holder: str, opening_deposit: float, pin: int):
        self.holder = holder
        self._balance = opening_deposit
        self.__pin = pin

    def deposit(self, amount: float) -> float:
        self._add_to_balance(amount)
        return self._balance

    def withdraw(self, amount: float, pin: int) -> bool:
        if not self.__verify_pin(pin):
            return False
        if amount > self._balance:
            return False
        self._add_to_balance(-amount)
        return True

    def get_balance(self, pin: int) -> float:
        if not self.__verify_pin(pin):
            return 0.0
        return self._balance

    def _add_to_balance(self, amount: float) -> None:
        self._balance += amount

    def __verify_pin(self, pin: int) -> bool:
        return pin == self.__pin
