class ValidationError extends haxe.Exception {
}

class StorageError extends haxe.Exception {
}

function parse_positive_int(value:Int):Int {
    if (value <= 0) {
        throw new ValidationError("not a positive integer");
    }
    return value;
}

class Repository {
    public var items:Array<String>;
    public function new():Void {
        this.items = [];
    }
    
    public function add(item:String):Void {
        if (item == "") {
            throw new ValidationError("empty item");
        }
        this.items.push(item);
    }
    
    public function get(index:Int):String {
        if (index < 0) {
            throw new StorageError("negative index");
        }
        if (index >= this.items.length) {
            throw new StorageError("index out of range");
        }
        return this.items[index];
    }
    
    public function safe_get(index:Int):String {
        try {
            return this.get(index);
        } catch (e:StorageError) {
            return "<missing>";
        }
    }
    
    public function safe_add(item:String):Bool {
        try {
            this.add(item);
            return true;
        } catch (e:ValidationError) {
            return false;
        } catch (e:haxe.Exception) {
            return false;
        }
    }
    
}

function find_or_default(repo:Repository, index:Int, default:String):String {
    try {
        return repo.get(index);
    } catch (e:haxe.Exception) {
        return default;
    }
}

function validate_and_process(items:Array<String>):Int {
    var successful:Int = 0;
    for (item in items) {
        try {
            if (item == "fail") {
                throw new ValidationError("explicit fail");
            }
            successful += 1;
        } catch (e:ValidationError) {
            successful += 0;
        }
    }
    return successful;
}

