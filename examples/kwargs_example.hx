// Strictly positional — emits as plain Haxe function.
// Has a default — emits as options-struct.
typedef ItemNewOptions = {
    name:String,
    ?unit_price:Float,
    ?quantity:Int
}

class Item {
    public var name:String;
    public var unit_price:Float;
    public var quantity:Int;
    // Has defaults — emits as options-struct constructor.
    public function new(options:ItemNewOptions):Void {
        var name:String = options.name;
        var unit_price:Float = (options.unit_price != null) ? options.unit_price : 0.0;
        var quantity:Int = (options.quantity != null) ? options.quantity : 0;
        this.name = name;
        this.unit_price = unit_price;
        this.quantity = quantity;
    }
    
    // No defaults — emits as plain positional method.
    public function total_value():Float {
        return this.unit_price * this.quantity;
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
    // Has defaults — options-struct, super() handled accordingly.
    public function new(options:DiscountedItemNewOptions):Void {
        var name:String = options.name;
        var unit_price:Float = (options.unit_price != null) ? options.unit_price : 0.0;
        var quantity:Int = (options.quantity != null) ? options.quantity : 0;
        var discount_percent:Float = (options.discount_percent != null) ? options.discount_percent : 0.0;
        super({ name: name, unit_price: unit_price, quantity: quantity });
        this.discount_percent = discount_percent;
    }
}

typedef GreetOptions = {
    name:String,
    ?greeting:String,
    ?excited:Bool
}

class KwargsExample {
    public static function add(a:Int, b:Int):Int {
        return a + b;
    }
    
    public static function greet(options:GreetOptions):String {
        var name:String = options.name;
        var greeting:String = (options.greeting != null) ? options.greeting : "Hello";
        var excited:Bool = (options.excited != null) ? options.excited : false;
        if (excited) {
            return greeting + ", " + name + "!";
        }
        return greeting + ", " + name;
    }
    
    public static function run():Int {
        // Positional call to positional function — direct mapping.
        var sum_value:Int = add(3, 5);
        // Out-of-order kwargs to positional function — reordered to positional.
        var sum_value2:Int = add(3, 5);
        // Positional call to options function — wrapped in literal.
        var msg1:String = greet({ name: "Derick" });
        // Kwargs to options function — passed as object literal.
        var msg2:String = greet({ name: "Derick", excited: true });
        // Mixed positional and kwarg to options function.
        var msg3:String = greet({ name: "Derick", excited: true });
        // Constructor with kwargs.
        var apple:Item = new Item({ name: "apple", unit_price: 0.5, quantity: 100 });
        // Constructor purely positional (still uses options form because
        // __init__ has defaults).
        var bread:Item = new Item({ name: "bread", unit_price: 2.5, quantity: 20 });
        // Subclass constructor with kwargs and super().
        var cake:DiscountedItem = new DiscountedItem({ name: "cake", unit_price: 10.0, quantity: 5, discount_percent: 25.0 });
        return sum_value + sum_value2;
    }
    
    public static function main():Void {}
}

