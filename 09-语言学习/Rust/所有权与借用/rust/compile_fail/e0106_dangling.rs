// E0106: missing lifetime specifier（悬垂引用在编译期被消灭）
//
// 观察方式： rustc e0106_dangling.rs
// 依据：The Book ch04-02 —— "If you have a reference to some data, the compiler
//       will ensure that the data will not go out of scope before the reference
//       to the data does."
//
// 原文报错：
//   error[E0106]: missing lifetime specifier
//    --> src/main.rs:5:16
//     |
//   5 | fn dangle() -> &String {
//     |                ^ expected named lifetime parameter
//     |
//     = help: this function's return type contains a borrowed value, but there is
//             no value for it to be borrowed from
//   help: consider using the `'static` lifetime, but this is uncommon unless
//         you're returning a borrowed value from a `const` or a `static`
//   help: instead, you are more likely to want to return an owned value
//
// 规避：返回 String（把所有权移出去），而不是 &String。

fn main() {
    let reference_to_nothing = dangle();
    println!("{reference_to_nothing}");
}

fn dangle() -> &String {
    // E0106
    let s = String::from("hello");
    &s
} // s 在这里被 drop —— 若允许返回，引用就悬垂了
