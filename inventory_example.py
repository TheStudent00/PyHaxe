"""
Disciplined Python example for the universal-language project.

A small inventory management program that follows the constraints
needed for mechanical translation to Haxe (and other targets).

Conformance checklist:
    1.  Type annotations on every parameter and return value
    2.  @haxe_extern decorator on wrapper classes (explicit boundary;
        body is manually maintained per-target)
    3.  Class-level field declarations with type annotations
    4.  Kwargs allowed at call sites (converter is signature-aware);
        kwargs-as-overloading inside method bodies is not
    5.  Single inheritance only
    6.  Decorators restricted to a known whitelist (@staticmethod,
        @haxe_extern)
    7.  No tuple unpacking
    8.  No `with` statements
    9.  No generators / yield
    10. No *args / **kwargs in signatures
    11. Named methods, not lambdas
    12. Broad exception catches only (Python-specific exception types
        don't survive translation)

Allowed because Haxe handles them well:
    -   `for x in collection` (Haxe: `for (x in collection)`)
    -   `for i in range(N)` (Haxe: `for (i in 0...N)`)
    -   `len(x)` (Haxe: `x.length`)
    -   String concatenation with + (Haxe has it)
    -   Simple list comprehensions (Haxe: `[for (x in items) f(x)]`)

Run with: python inventory_example.py
"""

from typing import List, Optional

from discipline import haxe_extern


# ============================================================
# Wrapper classes — explicit boundary for non-universal built-ins
# ============================================================

@haxe_extern()
class Print:
    """Wrapper around print so the I/O boundary is explicit.

    Marked @haxe_extern: the discipline checker skips this body, and
    the converter expects a hand-written Haxe equivalent. The Python
    body below is the Python-target implementation.
    """

    @staticmethod
    def line(message: str) -> None:
        print(message)


# ============================================================
# Domain classes — single inheritance shown via DiscountedItem
# ============================================================

class Item:
    """A single inventory item."""

    name: str
    unit_price: float
    quantity: int

    def __init__(
        self,
        name: str,
        unit_price: float = 0.0,
        quantity: int = 0,
    ) -> None:
        self.name = name
        self.unit_price = unit_price
        self.quantity = quantity

    def total_value(self) -> float:
        return self.unit_price * self.quantity

    def restock(self, amount: int) -> None:
        self.quantity += amount

    def sell(self, amount: int) -> bool:
        if amount > self.quantity:
            return False
        self.quantity -= amount
        return True

    def describe(self) -> str:
        return self.name + " x" + str(self.quantity)


class DiscountedItem(Item):
    """An item with a percentage discount on unit price."""

    discount_percent: float

    def __init__(
        self,
        name: str,
        unit_price: float = 0.0,
        quantity: int = 0,
        discount_percent: float = 0.0,
    ) -> None:
        super().__init__(name, unit_price, quantity)
        self.discount_percent = discount_percent

    def effective_price(self) -> float:
        multiplier: float = 1.0 - (self.discount_percent / 100.0)
        return self.unit_price * multiplier

    def total_value(self) -> float:
        return self.effective_price() * self.quantity

    def describe(self) -> str:
        base: str = self.name + " x" + str(self.quantity)
        return base + " (-" + str(self.discount_percent) + "%)"


class Inventory:
    """A collection of items with summary operations."""

    items: List[Item]

    def __init__(self) -> None:
        self.items = []

    def add_item(self, item: Item) -> None:
        self.items.append(item)

    def find_by_name(self, name: str) -> Optional[Item]:
        for current in self.items:
            if current.name == name:
                return current
        return None

    def total_value(self) -> float:
        total: float = 0.0
        for item in self.items:
            total += item.total_value()
        return total

    def count(self) -> int:
        return len(self.items)

    def describe_all(self) -> str:
        result: str = ""
        first: bool = True
        for item in self.items:
            if not first:
                result += ", "
            result += item.describe()
            first = False
        return result


# ============================================================
# Dispatch — kernel-style switch replacement
# ============================================================

class Commands:
    """Command dispatch for the inventory."""

    inventory: Inventory

    def __init__(self, inventory: Inventory) -> None:
        self.inventory = inventory

    def cmd_list(self) -> str:
        return self.inventory.describe_all()

    def cmd_total(self) -> str:
        return "Total value: " + str(self.inventory.total_value())

    def cmd_count(self) -> str:
        return "Item count: " + str(self.inventory.count())

    def cmd_unknown(self) -> str:
        return "Unknown command"

    def execute(self, command: str) -> str:
        # Value-keyed if/return chain replaces switch — translates to
        # the same form in any target.
        if command == "list":
            return self.cmd_list()
        if command == "total":
            return self.cmd_total()
        if command == "count":
            return self.cmd_count()
        return self.cmd_unknown()


# ============================================================
# Demo
# ============================================================

def run_demo() -> None:
    inventory: Inventory = Inventory()

    # Mixed positional and keyword construction — both translate to
    # positional Haxe calls when the converter is signature-aware.
    apple: Item = Item(name="apple", unit_price=0.50, quantity=100)
    bread: Item = Item("bread", 2.50, 20)
    cake: DiscountedItem = DiscountedItem(
        name="cake",
        unit_price=10.00,
        quantity=5,
        discount_percent=25.0,
    )

    inventory.add_item(apple)
    inventory.add_item(bread)
    inventory.add_item(cake)

    apple.restock(50)

    sold: bool = bread.sell(5)
    if not sold:
        Print.line("Not enough bread")

    commands: Commands = Commands(inventory)
    Print.line(commands.execute("list"))
    Print.line(commands.execute("count"))
    Print.line(commands.execute("total"))
    Print.line(commands.execute("nonsense"))

    found: Optional[Item] = inventory.find_by_name("apple")
    if found is not None:
        Print.line("Found: " + found.describe())

    # Try/except with a broad catch — Python-specific exception types
    # don't survive translation cleanly.
    try:
        missing: Optional[Item] = inventory.find_by_name("widget")
        if missing is None:
            raise Exception("Item not found: widget")
    except Exception as e:
        Print.line("Caught: " + str(e))


if __name__ == "__main__":
    run_demo()
