// E0502: cannot borrow `s` as mutable because it is also borrowed as immutable
//
// 观察方式： rustc e0502_mut_while_shared.rs
// 依据：The Book ch04-02 —— "Users of an immutable reference don't expect the value
//       to suddenly change out from under them!"
//
// 原文报错：
//   error[E0502]: cannot borrow `s` as mutable because it is also borrowed as immutable
//    --> src/main.rs:6:14
//     |
//   4 |     let r1 = &s; // no problem
//     |              -- immutable borrow occurs here
//   5 |     let r2 = &s; // no problem
//   6 |     let r3 = &mut s; // BIG PROBLEM
//     |              ^^^^^^ mutable borrow occurs here
//   8 |     println!("{r1}, {r2}, and {r3}");
//     |                -- immutable borrow later used here
//
// 规避（NLL 版）：把 r1/r2 的最后一次使用提前到创建 r3 之前即可编译——
//   见 main.rs 里的 nll_live_example()。

fn main() {
    let mut s = String::from("hello");

    let r1 = &s; // no problem
    let r2 = &s; // no problem
    let r3 = &mut s; // E0502

    println!("{r1}, {r2}, and {r3}");
}
