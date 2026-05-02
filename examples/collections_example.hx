class TodoList {
    public var items:Array<String>;
    public var priorities:Map<String, Int>;
    public function new():Void {
        this.items = [];
        this.priorities = new Map();
    }
    
    public function add(item:String, priority:Int):Void {
        this.items.push(item);
        this.priorities[item] = priority;
    }
    
    public function count():Int {
        return this.items.length;
    }
    
    public function get(index:Int):String {
        return this.items[index];
    }
    
    public function replace(index:Int, item:String):Void {
        this.items[index] = item;
    }
    
    public function total_priority():Int {
        var total:Int = 0;
        for (item in this.items) {
            total += this.priorities[item];
        }
        return total;
    }
    
    public function first_n_summary(n:Int):String {
        var result:String = "";
        for (i in 0...n) {
            if (i >= this.items.length) {
                break;
            }
            if (i > 0) {
                result += ", ";
            }
            result += this.items[i];
        }
        return result;
    }
    
}

function make_squares(n:Int):Array<Int> {
    var result:Array<Int> = [];
    for (i in 0...n) {
        result.push((i * i));
    }
    return result;
}

function sum_range(start:Int, stop:Int):Int {
    var total:Int = 0;
    for (i in start...stop) {
        total += i;
    }
    return total;
}

