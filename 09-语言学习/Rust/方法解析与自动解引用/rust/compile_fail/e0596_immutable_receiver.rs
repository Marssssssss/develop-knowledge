// E0596：查找阶段**不看**可变性，`&mut self` 方法照样被选中；
// 选完之后才发现接收者是不可变绑定，于是报错。
struct Counter {
    n: u32,
}

impl Counter {
    fn bump(&mut self) {
        self.n += 1;
    }
}

fn main() {
    let c = Counter { n: 0 }; // 少了 mut
    c.bump();                 // ERROR: cannot borrow `c` as mutable
}
