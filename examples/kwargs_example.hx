function add(a:Int, b:Int):Int {
    return (a + b);
}

typedef GreetOptions = {
    name:String,
    ?greeting:String,
    ?excited:Bool
}

function greet(options:GreetOptions):String {
    var name:String = options.name;
    var greeting:String = (options.greeting != null) ? options.greeting : "Hello";
    var excited:Bool = (options.excited != null) ? options.excited : false;
    if (excited) {
        return (((greeting + ", ") + name) + "!");
    }
    return ((greeting + ", ") + name);
}

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
        return (this.unit_price * this.quantity);
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
    
}

function run():Int {
    var sum_value:Int = add(3, 5);
    var sum_value2:Int = add(3, 5);
    var msg1:String = greet({ name: "Derick" });
    var msg2:String = greet({ name: "Derick", excited: true });
    var msg3:String = greet({ name: "Derick", excited: true });
    var apple:Item = new Item({ name: "apple", unit_price: 0.5, quantity: 100 });
    var bread:Item = new Item({ name: "bread", unit_price: 2.5, quantity: 20 });
    var cake:DiscountedItem = new DiscountedItem({ name: "cake", unit_price: 10.0, quantity: 5, discount_percent: 25.0 });
    return (sum_value + sum_value2);
}

