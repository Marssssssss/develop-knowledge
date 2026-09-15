//! 生命周期标注与省略规则（Rust demo 5/5）
//!
//! 生命周期标注**不改变**任何值的存活时间，它只是把「多个引用之间的存活关系」写进函数签名，
//! 成为借用检查器要遵守的契约。本 demo 用可断言的方式复现：
//!   · 三条省略规则（elision）分别命中时的写法
//!   · 结构体持有引用、`'static`、常量提升
//!   · 返回值生命周期取「两输入中较短者」的语义
//!
//! 依据：The Rust Programming Language ch10-03（Validating References with Lifetimes）、
//! Rust Reference *Destructors*（常量提升：`&None` 一律为 `&'static Option<_>`）。

use std::fmt::Display;

// ---------------------------------------------------------------------------
// 显式标注：两个输入同一生命周期 'a，返回也取 'a
// —— 语义上返回值的有效期 = 两个输入中**较短**的那个
// ---------------------------------------------------------------------------
fn longest<'a>(x: &'a str, y: &'a str) -> &'a str {
    if x.len() > y.len() {
        x
    } else {
        y
    }
}

// ---------------------------------------------------------------------------
// 省略规则 2：恰好一个输入生命周期 → 该生命周期赋给所有输出
// ---------------------------------------------------------------------------
fn first_word(s: &str) -> &str {
    // 等价于 fn first_word<'a>(s: &'a str) -> &'a str
    match s.find(' ') {
        Some(i) => &s[..i],
        None => s,
    }
}

// ---------------------------------------------------------------------------
// 省略规则 1：每个引用参数各自拿到一个生命周期参数（两个参数 → 两个不同生命周期）
// ---------------------------------------------------------------------------
fn describe(x: &str, y: &str) -> usize {
    // 输出不借用任何输入 → 无须标注
    x.len() + y.len()
}

// ---------------------------------------------------------------------------
// 结构体持有引用：必须标注；它不能比所借数据活得更久
// ---------------------------------------------------------------------------
struct ImportantExcerpt<'a> {
    part: &'a str,
}

impl<'a> ImportantExcerpt<'a> {
    /// 不需要返回引用 → 连 'a 都不用写（方法签名里的 'a 仍然必须声明）
    fn level(&self) -> i32 {
        3
    }

    /// 省略规则 3：多个输入生命周期但有 &self → 输出的生命周期取 self 的
    /// 等价于 fn announce_and_return_part<'b>(&'b self, announcement: &str) -> &'b str
    fn announce_and_return_part(&self, announcement: &str) -> &str {
        println!("      注意！{announcement}");
        self.part
    }
}

// 生命周期、泛型、trait bound 写在同一个尖括号列表里；where 子句可读性更好
fn longest_with_an_announcement<'a, T>(x: &'a str, y: &'a str, ann: T) -> &'a str
where
    T: Display,
{
    println!("      公告！{ann}");
    longest(x, y)
}

fn main() {
    println!("=== 1. 显式标注 vs 省略规则（行为完全一致）===");
    let s1 = String::from("long string is long");
    let s2 = String::from("xyz");
    println!("  longest(&s1, &s2)   = {:?}", longest(&s1, &s2));
    println!("  longest(&s2, &s1)   = {:?}", longest(&s2, &s1));
    assert_eq!(longest(&s1, &s2), "long string is long");

    println!("\n=== 2. 省略规则 2：单输入 → 输出取该输入 ===");
    println!("  first_word(\"hello world\") = {:?}", first_word("hello world"));
    println!("  first_word(\"nospace\")     = {:?}", first_word("nospace"));
    assert_eq!(first_word("hello world"), "hello");

    println!("\n=== 3. 省略规则 1：多输入且输出不借用 → 无需标注 ===");
    println!("  describe(\"abc\", \"de\") = {}", describe("abc", "de"));
    assert_eq!(describe("abc", "de"), 5);

    println!("\n=== 4. 结构体持引用 + 省略规则 3（方法）===");
    let novel = String::from("Call me Ishmael. Some years ago...");
    let first_sentence = novel.split('.').next().expect("找不到 '.'");
    let excerpt = ImportantExcerpt { part: first_sentence };
    println!("  excerpt.part = {:?}", excerpt.part);
    println!("  excerpt.level() = {}", excerpt.level());
    let announced = excerpt.announce_and_return_part("返回值借的是 self，不是 announcement");
    println!("  announce_and_return_part(...) = {announced:?}");
    assert_eq!(announced, "Call me Ishmael");
    assert_eq!(excerpt.level(), 3);

    println!("\n=== 5. 返回值的有效期 = 两输入中较短者 ===");
    let result; // 未初始化：让借用检查器来约束它的可用范围
    {
        let short = String::from("xyz");
        result = longest(s1.as_str(), short.as_str());
        // 只要在 short 存活期间使用就合法；把 println! 移到花括号外就是 E0597
        println!("  内层作用域内使用 result = {result:?}");
        assert_eq!(result, "long string is long");
    }
    println!("  → 一旦 short 离开作用域，result 也随之失效（E0597，见 compile_fail/）");

    println!("\n=== 6. 'static 与常量提升 ===");
    let s: &'static str = "I have a static lifetime."; // 字面量直接编进二进制
    println!("  字面量：{s}");
    let promoted: &'static Option<i32> = &None; // &None 被提升到 'static 槽位
    println!("  常量提升：&None 的类型是 &'static Option<i32> = {promoted:?}");
    assert!(promoted.is_none());

    println!("\n=== 7. 生命周期 + 泛型 + where 子句 ===");
    let out = longest_with_an_announcement(s1.as_str(), s2.as_str(), "三个参数各有各的类型");
    println!("  longest_with_an_announcement = {out:?}");
    assert_eq!(out, "long string is long");

    println!("\n=== 统计 ===");
    println!("  覆盖省略规则：规则 1（多输入各自独立）=describe，规则 2（单输入→输出）=first_word，");
    println!("                规则 3（方法 &self）=announce_and_return_part");
    println!("  生命周期标注不改变运行期行为：标注前后两个 longest 调用结果完全相同。");
    println!("\n断言全部通过。");
}
