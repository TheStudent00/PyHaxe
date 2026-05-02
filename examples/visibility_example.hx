class BankAccount {
    public var holder:String;
    private var balance:Float;
    private var pin:Int;
    public function new(holder:String, opening_deposit:Float, pin:Int):Void {
        this.holder = holder;
        this.balance = opening_deposit;
        this.pin = pin;
    }
    
    public function deposit(amount:Float):Float {
        this.add_to_balance(amount);
        return this.balance;
    }
    
    public function withdraw(amount:Float, pin:Int):Bool {
        if (!this.verify_pin(pin)) {
            return false;
        }
        if (amount > this.balance) {
            return false;
        }
        this.add_to_balance(-amount);
        return true;
    }
    
    public function get_balance(pin:Int):Float {
        if (!this.verify_pin(pin)) {
            return 0.0;
        }
        return this.balance;
    }
    
    private function add_to_balance(amount:Float):Void {
        this.balance += amount;
    }
    
    private function verify_pin(pin:Int):Bool {
        return pin == this.pin;
    }
    
}

