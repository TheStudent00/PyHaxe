class TypesExample {
    public static function first_or_none(items:Array<Int>):Null<Int> {
        if (items.length == 0) {
            return null;
        }
        return items[0];
    }
    
    public static function maybe_double(x:Null<Int>):Null<Int> {
        if (x == null) {
            return null;
        }
        return x * 2;
    }
    
    public static function stringify(value:haxe.extern.EitherType<Int, String>):String {
        return Std.string(value);
    }
    
    public static function label(value:haxe.extern.EitherType<Int, haxe.extern.EitherType<Float, String>>):String {
        return Std.string(value);
    }
    
    public static function passthrough(value:Dynamic):Dynamic {
        return value;
    }
    
    public static function apply_binop(op:(Int, Int) -> Int, a:Int, b:Int):Int {
        return op(a, b);
    }
    
    public static function stats(values:Array<Float>):Tuple2<Float, Int> {
        var total:Float = 0.0;
        for (v in values) {
            total += v;
        }
        return new Tuple2(total, values.length);
    }
    
    public static function lookup(table:Array<Tuple2<String, Int>>, key:String):Int {
        var i:Int = 0;
        while (i < table.length) {
            var entry:Tuple2<String, Int> = table[i];
            if (entry._0 == key) {
                return entry._1;
            }
            i += 1;
        }
        return -1;
    }
    
    public static function use_tuples():Int {
        var result:Tuple2<Float, Int> = stats([1.0, 2.0, 3.0]);
        var total:Float = result._0;
        var count:Int = result._1;
        return count;
    }
    
    public static function variable_index(t:Tuple3<Int, Int, Int>, i:Int):Int {
        return t.at(i);
    }
    
    public static function main():Void {}
}

