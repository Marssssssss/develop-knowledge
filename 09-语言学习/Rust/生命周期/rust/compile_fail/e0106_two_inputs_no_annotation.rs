// E0106: missing lifetime specifier —— 两个引用入参、返回引用，省略规则无法判定
//
// 观察方式： rustc e0106_two_inputs_no_annotation.rs
// 依据：The Book ch10-03 —— 省略规则只在下面三种情形成立：
//   规则 1：每个引用参数各自获得一个生命周期参数
//   规则 2：**恰好一个**输入生命周期 → 赋给所有输出
//   规则 3：多个输入，但其中一个是 &self / &mut self（仅方法）→ 输出的生命周期取 self 的
// 本例是自由函数（非方法）且有 2 个输入 → 三条规则都不适用 → 必须显式标注。
//
// 原文报错：
//   error[E0106]: missing lifetime specifier
//    --> src/main.rs:1:41
//     |
//   1 | fn longest(x: &str, y: &str) -> &str {
//     |               ----     ----     ^ expected named lifetime parameter
//     |
//     = help: this function's return type contains a borrowed value, but the signature
//             does not say whether it is borrowed from `x` or `y`
//   help: consider introducing a named lifetime parameter
//     |
//   1 | fn longest<'a>(x: &'a str, y: &'a str) -> &'a str {
//
// 修法：`fn longest<'a>(x: &'a str, y: &'a str) -> &'a str`（见 ../main.rs）。

fn main() {
    println!("{}", longest("abc", "defg"));
}

fn longest(x: &str, y: &str) -> &str {
    // E0106
    if x.len() > y.len() {
        x
    } else {
        y
    }
}
