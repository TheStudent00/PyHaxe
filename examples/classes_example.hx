extern class Print {
    public static function line(message:String):Void;
}

typedef CounterNewOptions = {
    ?start:Int,
    ?step:Int
}

class Counter {
    public var value:Int;
    public var step:Int;
    public function new(options:CounterNewOptions):Void {
        var start:Int = (options.start != null) ? options.start : 0;
        var step:Int = (options.step != null) ? options.step : 1;
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
        return new Counter({  });
    }
    
}

typedef BoundedCounterNewOptions = {
    ?start:Int,
    ?step:Int,
    ?maximum:Int
}

class BoundedCounter extends Counter {
    public var maximum:Int;
    public function new(options:BoundedCounterNewOptions):Void {
        var start:Int = (options.start != null) ? options.start : 0;
        var step:Int = (options.step != null) ? options.step : 1;
        var maximum:Int = (options.maximum != null) ? options.maximum : 100;
        super({ start: start, step: step });
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

