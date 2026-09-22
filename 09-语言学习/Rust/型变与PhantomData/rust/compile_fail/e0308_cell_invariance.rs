// Cell<T> / UnsafeCell<T> 在 T 上不变：内部可变性打破了「&T 只读」的前提，
// 于是共享引用之间的协变也必须被取消。
use std::cell::Cell;

fn shrink<'a>(c: &'a Cell<&'static str>) -> &'a Cell<&'a str> {
    c // ERROR: lifetime mismatch（`&'a Cell<&'static str>` 不是 `&'a Cell<&'a str>`）
}

fn main() {
    let c = Cell::new("hi");
    let r = shrink(&c);
    let short = String::from("x");
    r.set(&short);
}
