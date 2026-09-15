// 返回位 impl Trait 只能返回**单一具体类型**
//
// 观察方式： rustc impl_trait_two_types.rs
// 依据：The Book ch10-02 —— "However, you can only use `impl Trait` if you're returning
//       a single type. For example, this code that returns either a `NewsArticle` or a
//       `SocialPost` with the return type specified as `impl Summary` wouldn't work",
//       并给了原因："Returning either a `NewsArticle` or a `SocialPost` isn't allowed due
//       to restrictions around how the `impl Trait` syntax is implemented in the compiler."
//
// 报错要点：
//   error[E0308]: `if` and `else` have incompatible types
//     = note: expected opaque type `impl Summary`
//             found struct `SocialPost`
//
// 规避（Book 指向 Chapter 18 的 trait object）：
//   fn returns_summarizable(switch: bool) -> Box<dyn Summary> { ... }
//   —— 见 ../main.rs 的 make_any()。

trait Summary {
    fn summarize(&self) -> String;
}

struct NewsArticle;
struct SocialPost;

impl Summary for NewsArticle {
    fn summarize(&self) -> String {
        String::from("article")
    }
}

impl Summary for SocialPost {
    fn summarize(&self) -> String {
        String::from("post")
    }
}

fn returns_summarizable(switch: bool) -> impl Summary {
    if switch {
        NewsArticle
    } else {
        SocialPost // E0308：与 if 分支的 opaque type 不兼容
    }
}

fn main() {
    println!("{}", returns_summarizable(true).summarize());
}
