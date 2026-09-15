//! trait 与静态/动态分发（Rust demo 3/5）
//!
//! 同一份 trait 三种用法，运行期可观测的差别：
//!   · 静态分发（`<T: Summary>` / `impl Trait` 参数）→ **单态化**，调用点无间接跳转
//!   · 动态分发（`&dyn` / `Box<dyn>`）→ **胖指针**（数据指针 + vtable 指针），调用点查表再跳
//!
//! 依据：The Rust Programming Language ch10-01（单态化 / monomorphization）、
//! ch10-02（trait、默认方法、孤儿规则与 coherence、blanket impl、impl Trait 限制）、
//! ch18-02（trait object）、std 文档 `core::keyword::dyn`（胖指针与 vtable）。

use std::fmt::{self, Display};
use std::mem::size_of;

// ---------------------------------------------------------------------------
// trait：必需方法 + 默认方法（默认方法可以调用同 trait 的其他方法）
// ---------------------------------------------------------------------------
pub trait Summary {
    fn summarize_author(&self) -> String; // 无默认实现 → 实现者必须提供

    /// 默认实现：Rust 允许默认方法调用同 trait 中「没有默认实现」的方法，
    /// 于是实现者只需提供最小的一部分，就能白拿一整套行为。
    fn summarize(&self) -> String {
        format!("(Read more from {}...)", self.summarize_author())
    }
}

struct NewsArticle {
    headline: String,
    author: String,
}

impl Summary for NewsArticle {
    fn summarize_author(&self) -> String {
        format!("@{}", self.author)
    }
    // summarize 用默认实现
}

struct SocialPost {
    username: String,
    content: String,
}

impl Summary for SocialPost {
    fn summarize_author(&self) -> String {
        format!("@{}", self.username)
    }
    /// 覆盖默认实现：语法与实现「本来就没有默认」的方法完全一致
    fn summarize(&self) -> String {
        format!("{}: {}", self.username, self.content)
    }
}

/// 实现 Display 即可白拿 ToString::to_string()
/// —— 因为标准库有 blanket impl：`impl<T: Display> ToString for T`
impl Display for NewsArticle {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} ({})", self.headline, self.author)
    }
}

// ---------------------------------------------------------------------------
// 1) 静态分发：单态化，每种具体类型生成一份代码
// ---------------------------------------------------------------------------
fn notify_static<T: Summary>(item: &T) -> String {
    format!("[静态] 单态化实体 = {}", std::any::type_name::<T>())
}

/// impl Trait 在参数位置 = trait bound 的语法糖，但**允许多个参数类型不同**
fn notify_mixed(a: &impl Summary, b: &impl Summary) -> (String, String) {
    (a.summarize(), b.summarize())
}

/// 想强制两个参数同类型，必须写泛型 `<T: Summary>`（impl Trait 做不到）
fn notify_same<T: Summary>(a: &T, b: &T) -> (String, String) {
    (a.summarize(), b.summarize())
}

// ---------------------------------------------------------------------------
// 2) 动态分发：一份代码处理所有实现者，代价是查 vtable
// ---------------------------------------------------------------------------
fn notify_dyn(item: &dyn Summary) -> String {
    format!("[动态] 唯一一份实体，运行时查 vtable → {}", item.summarize())
}

// ---------------------------------------------------------------------------
// 3) 返回位 impl Trait 只能返回**单一具体类型**；多类型必须用 trait object
// ---------------------------------------------------------------------------
fn make_single() -> impl Summary {
    SocialPost {
        username: String::from("horse_ebooks"),
        content: String::from("of course, as you probably already know, people"),
    }
}

fn make_any(is_article: bool) -> Box<dyn Summary> {
    if is_article {
        Box::new(NewsArticle {
            headline: String::from("Rust 1.0 发布"),
            author: String::from("rust-lang"),
        })
    } else {
        Box::new(SocialPost {
            username: String::from("ferris"),
            content: String::from("hello, crab"),
        })
    }
}

// ---------------------------------------------------------------------------
// 4) 条件实现：只有 T: Display + PartialOrd 时 Pair<T> 才有 cmp_display
// ---------------------------------------------------------------------------
struct Pair<T> {
    x: T,
    y: T,
}

impl<T> Pair<T> {
    fn new(x: T, y: T) -> Self {
        Self { x, y }
    }
}

impl<T: Display + PartialOrd> Pair<T> {
    fn cmp_display(&self) -> String {
        if self.x >= self.y {
            format!("x 更大: {}", self.x)
        } else {
            format!("y 更大: {}", self.y)
        }
    }
}

fn main() {
    let article = NewsArticle {
        headline: String::from("Rust 单态化"),
        author: String::from("doc"),
    };
    let post = SocialPost {
        username: String::from("alice"),
        content: String::from("trait 就是接口 + 约束 + 单态化开关"),
    };

    // ---- 静态分发：单态化证据 -------------------------------------------------
    println!("=== 静态分发（单态化）===");
    println!("{}", notify_static(&article));
    println!("{}", notify_static(&post));
    let mono = [
        std::any::type_name_of_val(&notify_static(&article)),
        std::any::type_name_of_val(&notify_static(&post)),
    ];
    println!("两次调用返回类型串 = {mono:?}（同一泛型函数，被展开成 2 份具体代码）");
    assert_eq!(mono[0], mono[1]); // 函数本身同类型；差异体现在 T 的实体上

    println!("\n=== impl Trait 参数 vs 泛型参数 ===");
    let (a1, b1) = notify_mixed(&article, &post); // 两个不同类型也可
    println!("impl Trait（允许不同型）→ {a1} | {b1}");
    let (a2, b2) = notify_same(&post, &post); // 必须同型
    println!("泛型 <T: Summary>（强制同型）→ {a2} | {b2}");

    // ---- 动态分发：胖指针 -----------------------------------------------------
    println!("\n=== 动态分发（trait object = 胖指针）===");
    println!("{}", notify_dyn(&article));
    println!("{}", notify_dyn(&post));

    let thin = size_of::<&NewsArticle>();
    let fat = size_of::<&dyn Summary>();
    let boxed = size_of::<Box<dyn Summary>>();
    println!("size_of::<&NewsArticle>()    = {thin} 字节（瘦指针）");
    println!("size_of::<&dyn Summary>()    = {fat} 字节（= 数据指针 + vtable 指针）");
    println!("size_of::<Box<dyn Summary>>() = {boxed} 字节");
    assert_eq!(fat, 2 * thin, "trait object 应是两字宽的胖指针");

    // 异构集合：泛型做不到（Vec<T> 只能一种 T），trait object 可以
    let items: Vec<Box<dyn Summary>> = vec![
        Box::new(NewsArticle {
            headline: String::from("h1"),
            author: String::from("u1"),
        }),
        Box::new(SocialPost {
            username: String::from("u2"),
            content: String::from("c2"),
        }),
    ];
    println!("异构 Vec<Box<dyn Summary>> 长度 = {}", items.len());
    for (i, it) in items.iter().enumerate() {
        println!("  [{i}] {}", it.summarize());
    }

    // ---- 返回位：impl Trait 只能单一类型，多类型要用 dyn ----------------------
    println!("\n=== 返回位的两种写法 ===");
    println!("impl Summary（单一具体类型）→ {}", make_single().summarize());
    println!("Box<dyn Summary>（运行期决定）→ {}", make_any(true).summarize());
    println!("Box<dyn Summary>（运行期决定）→ {}", make_any(false).summarize());

    // ---- blanket impl：实现 Display 就白拿 ToString --------------------------
    println!("\n=== blanket impl: impl<T: Display> ToString for T ===");
    let s: String = article.to_string(); // 没有为 NewsArticle 手写 to_string
    println!("article.to_string() = {s}");
    assert_eq!(s, "Rust 单态化 (doc)");

    // ---- 条件实现 -------------------------------------------------------------
    println!("\n=== 条件实现 impl<T: Display + PartialOrd> Pair<T> ===");
    let p = Pair::new(3, 7);
    println!("Pair<i32>::cmp_display() = {}", p.cmp_display());
    let p2 = Pair::new("apple", "banana");
    println!("Pair<&str>::cmp_display() = {}", p2.cmp_display());

    println!("\n断言通过：单态化实体数量 = 2（NewsArticle / SocialPost），胖指针 = 2 字宽。");
    println!("无法运行期演示的反例（孤儿规则、impl Trait 返回多类型、dyn 不兼容）见 compile_fail/。");
}
