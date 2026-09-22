// E0034：同一个候选类型上有两个 trait 提供同名方法 → multiple applicable items
struct S;

trait Alpha {
    fn dup(&self) -> String;
}
trait Beta {
    fn dup(&self) -> String;
}

impl Alpha for S {
    fn dup(&self) -> String { String::from("alpha") }
}
impl Beta for S {
    fn dup(&self) -> String { String::from("beta") }
}

fn main() {
    let s = S;
    println!("{}", s.dup()); // ERROR: multiple applicable items in scope
                             // 修法：Alpha::dup(&s) / <S as Beta>::dup(&s)
}
