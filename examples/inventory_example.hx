// ============================================================
// Wrapper classes — explicit boundary for non-universal built-ins
// ============================================================
extern class Print {
    public static function line(message:String):Void;
}

// ============================================================
// Domain classes — single inheritance shown via DiscountedItem
// ============================================================
typedef ItemNewOptions = {
    name:String,
    ?unit_price:Float,
    ?quantity:Int
}

class Item {
    public var name:String;
    public var unit_price:Float;
    public var quantity:Int;
    public function new(options:ItemNewOptions):Void {
        var name:String = options.name;
        var unit_price:Float = (options.unit_price != null) ? options.unit_price : 0.0;
        var quantity:Int = (options.quantity != null) ? options.quantity : 0;
        this.name = name;
        this.unit_price = unit_price;
        this.quantity = quantity;
    }
    
    public function total_value():Float {
        return this.unit_price * this.quantity;
    }
    
    public function restock(amount:Int):Void {
        this.quantity += amount;
    }
    
    public function sell(amount:Int):Bool {
        if (amount > this.quantity) {
            return false;
        }
        this.quantity -= amount;
        return true;
    }
    
    public function describe():String {
        return this.name + " x" + Std.string(this.quantity);
    }
}

typedef DiscountedItemNewOptions = {
    name:String,
    ?unit_price:Float,
    ?quantity:Int,
    ?discount_percent:Float
}

class DiscountedItem extends Item {
    public var discount_percent:Float;
    public function new(options:DiscountedItemNewOptions):Void {
        var name:String = options.name;
        var unit_price:Float = (options.unit_price != null) ? options.unit_price : 0.0;
        var quantity:Int = (options.quantity != null) ? options.quantity : 0;
        var discount_percent:Float = (options.discount_percent != null) ? options.discount_percent : 0.0;
        super({ name: name, unit_price: unit_price, quantity: quantity });
        this.discount_percent = discount_percent;
    }
    
    public function effective_price():Float {
        var multiplier:Float = 1.0 - this.discount_percent / 100.0;
        return this.unit_price * multiplier;
    }
    
    override public function total_value():Float {
        return this.effective_price() * this.quantity;
    }
    
    override public function describe():String {
        var base:String = this.name + " x" + Std.string(this.quantity);
        return base + " (-" + Std.string(this.discount_percent) + "%)";
    }
}

class Inventory {
    public var items:Array<Item>;
    public function new():Void {
        this.items = [];
    }
    
    public function add_item(item:Item):Void {
        this.items.push(item);
    }
    
    public function find_by_name(name:String):Null<Item> {
        for (current in this.items) {
            if (current.name == name) {
                return current;
            }
        }
        return null;
    }
    
    public function total_value():Float {
        var total:Float = 0.0;
        for (item in this.items) {
            total += item.total_value();
        }
        return total;
    }
    
    public function count():Int {
        return this.items.length;
    }
    
    public function describe_all():String {
        var result:String = "";
        var first:Bool = true;
        for (item in this.items) {
            if (!first) {
                result += ", ";
            }
            result += item.describe();
            first = false;
        }
        return result;
    }
}

// ============================================================
// Dispatch — kernel-style switch replacement
// ============================================================
class Commands {
    public var inventory:Inventory;
    public function new(inventory:Inventory):Void {
        this.inventory = inventory;
    }
    
    public function cmd_list():String {
        return this.inventory.describe_all();
    }
    
    public function cmd_total():String {
        return "Total value: " + Std.string(this.inventory.total_value());
    }
    
    public function cmd_count():String {
        return "Item count: " + Std.string(this.inventory.count());
    }
    
    public function cmd_unknown():String {
        return "Unknown command";
    }
    
    public function execute(command:String):String {
        // Value-keyed if/return chain replaces switch — translates to
        // the same form in any target.
        if (command == "list") {
            return this.cmd_list();
        }
        if (command == "total") {
            return this.cmd_total();
        }
        if (command == "count") {
            return this.cmd_count();
        }
        return this.cmd_unknown();
    }
}

class InventoryExample {
    public static function run_demo():Void {
        // ============================================================
        // Demo
        // ============================================================
        var inventory:Inventory = new Inventory();
        // Mixed positional and keyword construction — both translate to
        // positional Haxe calls when the converter is signature-aware.
        var apple:Item = new Item({ name: "apple", unit_price: 0.5, quantity: 100 });
        var bread:Item = new Item({ name: "bread", unit_price: 2.5, quantity: 20 });
        var cake:DiscountedItem = new DiscountedItem({ name: "cake", unit_price: 10.0, quantity: 5, discount_percent: 25.0 });
        inventory.add_item(apple);
        inventory.add_item(bread);
        inventory.add_item(cake);
        apple.restock(50);
        var sold:Bool = bread.sell(5);
        if (!sold) {
            Print.line("Not enough bread");
        }
        var commands:Commands = new Commands(inventory);
        Print.line(commands.execute("list"));
        Print.line(commands.execute("count"));
        Print.line(commands.execute("total"));
        Print.line(commands.execute("nonsense"));
        var found:Null<Item> = inventory.find_by_name("apple");
        if (found != null) {
            Print.line("Found: " + found.describe());
        }
        // Try/except with a broad catch — Python-specific exception types
        // don't survive translation cleanly.
        try {
            var missing:Null<Item> = inventory.find_by_name("widget");
            if (missing == null) {
                throw new haxe.Exception("Item not found: widget");
            }
        } catch (e:haxe.Exception) {
            Print.line("Caught: " + Std.string(e));
        }
    }
    
    public static function main():Void {
        run_demo();
    }
}

