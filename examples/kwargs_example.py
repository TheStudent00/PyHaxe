"""
kwargs_example.py

Milestone 4 demonstration program. Exercises the kwargs translation
strategy:
    - Functions/methods with default values -> options-struct
    - Functions/methods with no defaults -> plain positional
    - Mixed positional and kwarg call sites
    - Out-of-order kwargs
    - super().__init__() resolution
"""


# Strictly positional — emits as plain Haxe function.
def add(a: int, b: int) -> int:
    return a + b


# Has a default — emits as options-struct.
def greet(name: str, greeting: str = "Hello", excited: bool = False) -> str:
    if excited:
        return greeting + ", " + name + "!"
    return greeting + ", " + name


class Item:
    name: str
    unit_price: float
    quantity: int

    # Has defaults — emits as options-struct constructor.
    def __init__(self, name: str, unit_price: float = 0.0, quantity: int = 0):
        self.name = name
        self.unit_price = unit_price
        self.quantity = quantity

    # No defaults — emits as plain positional method.
    def total_value(self) -> float:
        return self.unit_price * self.quantity


class DiscountedItem(Item):
    discount_percent: float

    # Has defaults — options-struct, super() handled accordingly.
    def __init__(self, name: str, unit_price: float = 0.0,
                 quantity: int = 0, discount_percent: float = 0.0):
        super().__init__(name, unit_price, quantity)
        self.discount_percent = discount_percent


def run() -> int:
    # Positional call to positional function — direct mapping.
    sum_value: int = add(3, 5)

    # Out-of-order kwargs to positional function — reordered to positional.
    sum_value2: int = add(b=5, a=3)

    # Positional call to options function — wrapped in literal.
    msg1: str = greet("Derick")

    # Kwargs to options function — passed as object literal.
    msg2: str = greet(name="Derick", excited=True)

    # Mixed positional and kwarg to options function.
    msg3: str = greet("Derick", excited=True)

    # Constructor with kwargs.
    apple: Item = Item(name="apple", unit_price=0.50, quantity=100)

    # Constructor purely positional (still uses options form because
    # __init__ has defaults).
    bread: Item = Item("bread", 2.50, 20)

    # Subclass constructor with kwargs and super().
    cake: DiscountedItem = DiscountedItem(
        name="cake", unit_price=10.00, quantity=5, discount_percent=25.0)

    return sum_value + sum_value2
