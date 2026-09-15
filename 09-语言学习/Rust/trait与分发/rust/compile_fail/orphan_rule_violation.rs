// 孤儿规则 / coherence：不能为「外部类型」实现「外部 trait」
//
// 观察方式： rustc orphan_rule_violation.rs
// 依据：The Book ch10-02 —— "we can implement a trait on a type only if either the
//       trait or the type, or both, are local to our crate. ... But we can't implement
//       external traits on external types. ... This restriction is part of a property
//       called *coherence*, and more specifically the *orphan rule*, so named because
//       the parent type is not present."
//
// 报错要点（rustc 的措辞随版本略有变化，核心是下面两句）：
//   error[E0117]: only traits defined in the current crate can be implemented for
//                 types defined in the current crate
//     = note: `Vec<i32>` is not defined in the current crate
//     = note: implement this trait for a type defined in the current crate
//
// 允许 / 禁止的组合（trait 与类型各自是否「本地」）：
//   本地 trait + 本地类型  → ✅
//   本地 trait + 外部类型  → ✅（如本 crate 里为 Vec<T> 实现自定义 trait）
//   外部 trait + 本地类型  → ✅（如为自定义结构体实现 Display）
//   外部 trait + 外部类型  → ❌ 本文件演示的就是这一格
//
// 为什么需要：否则两个 crate 可以给同一类型实现同一 trait，编译器无法确定用哪个实现。

use std::fmt::Display;

fn main() {
    println!("本文件用于观察 E0117，无法编译通过");
}

// ❌ 外部 trait（Display）+ 外部类型（Vec<T>）
impl<T: Display> Display for Vec<T> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{self:?}")
    }
}
