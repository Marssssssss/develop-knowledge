// E0596: cannot borrow `*some_string` as mutable, as it is behind a `&` reference
//
// 观察方式： rustc e0596_mutate_behind_shared.rs
// 依据：The Book ch04-02 —— "Just as variables are immutable by default, so are
//       references. We're not allowed to modify something we have a reference to."
//
// 原文报错：
//   error[E0596]: cannot borrow `*some_string` as mutable, as it is behind a `&` reference
//    --> src/main.rs:8:5
//     |
//   8 |     some_string.push_str(", world");
//     |     ^^^^^^^^^^^ `some_string` is a `&` reference, so the data it refers to
//     |                 cannot be borrowed as mutable
//     |
//   help: consider changing this to be a mutable reference
//     |
//   7 | fn change(some_string: &mut String) {
//     |                         +++
//
// 规避：签名改成 &mut String，调用点改成 change(&mut s)（且 s 必须声明为 mut）。

fn main() {
    let s = String::from("hello");
    change(&s);
}

fn change(some_string: &String) {
    some_string.push_str(", world"); // E0596
}
