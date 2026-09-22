// trait 高级形态：关联类型 / supertrait / GAT / dyn 兼容
// The Book ch20-02 + Reference items/traits（Dyn compatibility）
//          + Reference items/associated-items（GAT）

use std::fmt::{self, Display};
use std::rc::Rc;

// ---------------------------------------------------------------- 1. 关联类型
// 与泛型参数的区别（The Book）：泛型参数版可以对同一类型实现**多次**，
// 于是调用方必须标注用的是哪一份；关联类型版只能实现**一次**，调用方零标注。
pub trait IteratorAssoc {
    type Item;                       // 占位类型，实现者指定
    fn next(&mut self) -> Option<Self::Item>;
}

struct Counter {
    count: u32,
}

impl IteratorAssoc for Counter {
    type Item = u32;
    fn next(&mut self) -> Option<u32> {
        self.count += 1;
        if self.count < 3 { Some(self.count) } else { None }
    }
}

// ---------------------------------------------------------------- 2. supertrait
// `OutlinePrint: Display` 表示实现 OutlinePrint 必须先实现 Display；
// 于是方法体里可以直接用 Display 带来的 to_string()。
trait OutlinePrint: Display {
    fn outline_print(&self) {
        let output = self.to_string();
        let len = output.len();
        println!("{}", "*".repeat(len + 4));
        println!("*{}*", " ".repeat(len + 2));
        println!("* {output} *");
        println!("*{}*", " ".repeat(len + 2));
        println!("{}", "*".repeat(len + 4));
    }
}

struct Point {
    x: i32,
    y: i32,
}

impl Display for Point {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "({}, {})", self.x, self.y)
    }
}

impl OutlinePrint for Point {}

// supertrait 是**传递**的：bound 一个子 trait 就能用全部祖先的关联项
fn print_anything<T: OutlinePrint>(t: &T) {
    t.outline_print();      // 子 trait 的方法
    println!("{}", t);      // 来自 supertrait Display
}

// ---------------------------------------------------------------- 3. GAT
// 关联类型可以带泛型参数与 where 子句（Reference 原文例子）。
struct ArrayLender<'a, T>(&'a mut [T; 16]);

trait Lend {
    type Lender<'a>
    where
        Self: 'a;
    fn lend<'a>(&'a mut self) -> Self::Lender<'a>;
}

impl<T> Lend for [T; 16] {
    type Lender<'a> = ArrayLender<'a, T>
    where
        Self: 'a;
    fn lend<'a>(&'a mut self) -> ArrayLender<'a, T> {
        ArrayLender(self)
    }
}

fn borrow<'a, T: Lend>(array: &'a mut T) -> <T as Lend>::Lender<'a> {
    array.lend()
}

// ---------------------------------------------------------------- 4. dyn 兼容
// 只有 &self / &mut self / Box<Self> / Rc<Self> / Arc<Self> / Pin<P> 这类接收者，
// 且不带类型参数、不返回 Self、不是 async fn、不带关联常量与 GAT 的 trait，
// 才能做 trait object 的基 trait。
trait Shape {
    fn area(&self) -> f64;
}

struct Square(f64);
impl Shape for Square {
    fn area(&self) -> f64 { self.0 * self.0 }
}

// 关联类型**不带**泛型时依然是 dyn 兼容的，但必须在类型里写全
fn use_dyn_iter(it: &mut dyn IteratorAssoc<Item = u32>) {
    while let Some(v) = it.next() {
        print!("{v} ");
    }
}

// 显式不可分派：加 `where Self: Sized` 就能让「返回 Self」的方法与 trait object 共存
trait Factory {
    fn make(&self) -> Self
    where
        Self: Sized;
    fn name(&self) -> &str;
}

impl Factory for Square {
    fn make(&self) -> Square { Square(self.0) }
    fn name(&self) -> &str { "square" }
}

fn use_dyn_factory(f: &dyn Factory) {
    println!("{}", f.name()); // 只有可分派的那个方法能调
}

fn main() {
    print_anything(&Point { x: 1, y: 3 });

    let mut c = Counter { count: 0 };
    while let Some(v) = c.next() {
        print!("{v} ");
    }
    println!();

    let mut array = [0usize; 16];
    let _lender = borrow(&mut array);

    let shapes: Vec<Rc<dyn Shape>> = vec![Rc::new(Square(2.0)), Rc::new(Square(3.0))];
    for s in &shapes {
        print!("{} ", s.area());
    }
    println!();

    let mut it = Counter { count: 0 };
    use_dyn_iter(&mut it);
    println!();
    use_dyn_factory(&Square(1.0));
}
