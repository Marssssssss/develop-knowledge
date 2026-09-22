// E0038：带关联常量的 trait 不是 dyn 兼容（旧称 object safe），
// 不能做成 trait object。
trait WithConst {
    const MAX: i32;
    fn area(&self) -> f64;
}

struct S;

impl WithConst for S {
    const MAX: i32 = 10;
    fn area(&self) -> f64 { 1.0 }
}

fn main() {
    let obj: Box<dyn WithConst> = Box::new(S); // ERROR: the trait cannot be made into an object
}
