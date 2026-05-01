function add(a:Int, b:Int):Int {
    var result:Int = (a + b);
    return result;
}

function classify(value:Int):String {
    if (value > 0) {
        return "positive";
    } else if (value < 0) {
        return "negative";
    } else {
        return "zero";
    }
}

function repeat(text:String, count:Int):String {
    var result:String = "";
    var i:Int = 0;
    while (i < count) {
        result = (result + text);
        i += 1;
    }
    return result;
}

function safe_divide(numerator:Float, denominator:Float):Float {
    if (denominator == 0) {
        return 0.0;
    }
    return (numerator / denominator);
}

function in_range(value:Int, low:Int, high:Int):Bool {
    if (value < low) {
        return false;
    }
    if (value > high) {
        return false;
    }
    return true;
}

function in_range_combined(value:Int, low:Int, high:Int):Bool {
    return (value >= low && value <= high);
}

