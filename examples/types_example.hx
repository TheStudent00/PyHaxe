class Tuple2<T0, T1> {
    public var _0:T0;
    public var _1:T1;
    private var _items:Array<Dynamic>;
    public function new(_0:T0, _1:T1):Void {
        this._0 = _0;
        this._1 = _1;
    }
    public function at(i:Int):Dynamic {
        if (this._items == null) this._items = [this._0, this._1];
        return this._items[i];
    }
    public function iterator():Iterator<Dynamic> {
        if (this._items == null) this._items = [this._0, this._1];
        return this._items.iterator();
    }
    public function equals(other:Tuple2<T0, T1>):Bool {
        return this._0 == other._0 && this._1 == other._1;
    }
}

class Tuple3<T0, T1, T2> {
    public var _0:T0;
    public var _1:T1;
    public var _2:T2;
    private var _items:Array<Dynamic>;
    public function new(_0:T0, _1:T1, _2:T2):Void {
        this._0 = _0;
        this._1 = _1;
        this._2 = _2;
    }
    public function at(i:Int):Dynamic {
        if (this._items == null) this._items = [this._0, this._1, this._2];
        return this._items[i];
    }
    public function iterator():Iterator<Dynamic> {
        if (this._items == null) this._items = [this._0, this._1, this._2];
        return this._items.iterator();
    }
    public function equals(other:Tuple3<T0, T1, T2>):Bool {
        return this._0 == other._0 && this._1 == other._1 && this._2 == other._2;
    }
}

function first_or_none(items:Array<Int>):Null<Int> {
    if (items.length == 0) {
        return null;
    }
    return items[0];
}

function maybe_double(x:Null<Int>):Null<Int> {
    if (x == null) {
        return null;
    }
    return (x * 2);
}

function stringify(value:haxe.extern.EitherType<Int, String>):String {
    return Std.string(value);
}

function label(value:haxe.extern.EitherType<Int, haxe.extern.EitherType<Float, String>>):String {
    return Std.string(value);
}

function passthrough(value:Dynamic):Dynamic {
    return value;
}

function apply_binop(op:(Int, Int) -> Int, a:Int, b:Int):Int {
    return op(a, b);
}

function stats(values:Array<Float>):Tuple2<Float, Int> {
    var total:Float = 0.0;
    for (v in values) {
        total += v;
    }
    return new Tuple2(total, values.length);
}

function lookup(table:Array<Tuple2<String, Int>>, key:String):Int {
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

function use_tuples():Int {
    var result:Tuple2<Float, Int> = stats([1.0, 2.0, 3.0]);
    var total:Float = result._0;
    var count:Int = result._1;
    return count;
}

function variable_index(t:Tuple3<Int, Int, Int>, i:Int):Int {
    return t.at(i);
}

