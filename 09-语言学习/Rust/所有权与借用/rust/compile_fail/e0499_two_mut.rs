// E0499: cannot borrow `s` as mutable more than once at a time
//
// 观察方式： rustc e0499_two_mut.rs
// 依据：The Book ch04-02 —— 借用规则第一条："At any given time, you can have
//       *either* one mutable reference *or* any number of immutable references."
//
// 原文报错：
//   error[E0499]: cannot borrow `s` as mutable more than once at a time
//    --> src/main.rs:5:14
//     |
//   4 |     let r1 = &mut s;
//     |              ------ first mutable borrow occurs here
//   5 |     let r2 = &mut s;
//     |              ^^^^^^ second mutable borrow occurs here
//   7 |     println!("{r1}, {r2}");
//     |                -- first borrow later used here
//
// 规避：让两个可变借用的**作用域不重叠**（花括号或 NLL），
//       或把 r1 的最后一次使用挪到 r2 之前。

fn main() {
    let mut s = String::from("hello");

    let r1 = &mut s;
    let r2 = &mut s; // E0499

    println!("{r1}, {r2}");
}
