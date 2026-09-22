// 方法调用解析：候选接收者列表 + 自动解引用/自动借用
// 依据 Rust Reference `expressions/method-call-expr`。
// 候选列表 = 反复解引用，最后做一次未定长强制转换；
//           然后对每个 `T` 紧跟 `&T` 与 `&mut T`。
// 搜索 = 按候选顺序，先固有方法，再 trait 方法（类型参数时 bound 优先）。

// ---------------------------------------------------------------- 1. 官方的「出人意料」
// Reference NOTE：因为 `&self` 方法在候选列表里排在 `&mut self` 之前，
// 所以 trait 方法会被先找到，struct 自己的方法反而落选。
struct Foo {}

trait Bar {
    fn bar(&self);
}

impl Foo {
    fn bar(&mut self) {
        println!("In struct impl!")
    }
}

impl Bar for Foo {
    fn bar(&self) {
        println!("In trait impl!")
    }
}

// ---------------------------------------------------------------- 2. 自动解引用
// String 没有 `len`，但 `Deref<Target = str>` 把 `str` 加进候选列表。
fn deref_len() {
    let s = String::from("hello");
    // 候选：String, &String, &mut String, str, &str, &mut str
    // `str::len(&self)` 的接收者类型是 `&str`，在 #4 命中。
    println!("len = {}", s.len());
}

// ---------------------------------------------------------------- 3. Box 的两级解引用
fn box_deref() {
    let b: Box<String> = Box::new(String::from("boxed"));
    // 候选：Box<String>, &Box<String>, &mut Box<String>, String, &String,
    //      &mut String, str, &str, &mut str
    println!("len = {}", b.len());
}

// ---------------------------------------------------------------- 4. 数组 → 切片
fn array_unsize() {
    let a: Box<[i32; 2]> = Box::new([1, 2]);
    // 候选（官方例子原文）：
    //   Box<[i32;2]>, &Box<[i32;2]>, &mut Box<[i32;2]>,
    //   [i32;2], &[i32;2], &mut [i32;2],
    //   [i32]（未定长强制转换）, &[i32], &mut [i32]
    println!("first = {}", a[0]);
}

// ---------------------------------------------------------------- 5. 类型参数：bound 优先
// 官方：「If T is a type parameter, methods provided by trait bounds on T
//        are looked up first.」
trait MyClone {
    fn dup(&self) -> String;
}

fn generic_dup<T: Clone + MyClone>(t: &T) -> String {
    // 两个 trait 都提供 `dup` 时，bound 的写法决定谁被先查；
    // 真正冲突的是「两个都在 bound 里」——那时必须完全限定。
    t.dup()
}

// ---------------------------------------------------------------- 6. 完全限定语法
fn disambiguate(f: &mut Foo) {
    Foo::bar(f);            // 明确要 struct 的 &mut self 版本
    <Foo as Bar>::bar(f);   // 明确要 trait 的 &self 版本
}

// ---------------------------------------------------------------- 7. trait object 同名
// 官方 WARNING：`dyn Trait` 上若固有方法与 trait 方法同名，
// 方法调用表达式直接报错；完全限定语法只会命中 trait 方法，
// 「There is no way to call the inherent method」。
trait Show {
    fn show(&self);
}

impl dyn Show {
    fn show(&self) {
        println!("inherent on dyn Show")
    }
}

struct Card;

impl Show for Card {
    fn show(&self) {
        println!("trait impl")
    }
}

// ---------------------------------------------------------------- 8. 数组的 edition 差异
// 2021 之前：候选是数组类型时，`IntoIterator` 提供的方法被忽略，
// 于是 `array.into_iter()` 退到 `&[T; N]` 上，迭代出**引用**。
// 2021 起：不再忽略，直接按值迭代。
fn array_iter() {
    let a = [1, 2, 3];
    for x in a.into_iter() {
        let _: i32 = x; // 2021 起这里是 i32（2018 里是 &i32）
    }
}

fn main() {
    let mut f = Foo {};
    f.bar();        // 打印 "In trait impl!" —— 不是 struct 的版本
    disambiguate(&mut f);
    deref_len();
    box_deref();
    array_unsize();
    array_iter();

    let c = Card;
    let d: &dyn Show = &c;
    // d.show();                 // 错误：固有与 trait 同名
    <dyn Show as Show>::show(d); // 只能这样，且命中的是 trait 版本

    println!("generic = {}", generic_dup(&String::from("x")));
}
