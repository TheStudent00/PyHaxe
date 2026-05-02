extern class Print {
    static function line(message:String):Void;
}

class Counter {
    public var value:Int;
    public var step:Int;
    public function new(start:Int = 0, step:Int = 1):Void {
        this.value = start;
        this.step = step;
    }
    
    public function increment():Int {
        this.value += this.step;
        return this.value;
    }
    
    public function reset():Void {
        this.value = 0;
    }
    
    public static function make_default():Counter {
        return new Counter();
    }
    
}

class BoundedCounter extends Counter {
    public var maximum:Int;
    public function new(start:Int = 0, step:Int = 1, maximum:Int = 100):Void {
        super(start, step);
        this.maximum = maximum;
    }
    
    override public function increment():Int {
        var next_value:Int = (this.value + this.step);
        if (next_value > this.maximum) {
            this.value = this.maximum;
        } else {
            this.value = next_value;
        }
        return this.value;
    }
    
}

