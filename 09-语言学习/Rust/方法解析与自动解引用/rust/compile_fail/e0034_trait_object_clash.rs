// trait object 上固有方法与 trait 方法同名：方法调用表达式直接报错。
// 官方 WARNING：即使用完全限定语法，命中的也是 trait 方法，
// 「There is no way to call the inherent method」。
trait Show {
    fn show(&self);
}

impl dyn Show {
    fn show(&self) {
        println!("inherent")
    }
}

struct Card;

impl Show for Card {
    fn show(&self) {
        println!("trait")
    }
}

fn main() {
    let obj: &dyn Show = &Card;
    obj.show(); // ERROR: 固有与 trait 同名，无法用方法调用表达式解析
}
