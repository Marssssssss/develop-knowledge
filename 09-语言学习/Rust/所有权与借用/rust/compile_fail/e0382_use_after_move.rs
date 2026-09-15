// E0382: borrow of moved value
//
// 观察方式： rustc e0382_use_after_move.rs
// 依据：The Book ch04-01 —— String 只复制栈上的 ptr/len/cap，堆数据不复制；
//       Rust 让 s1 失效以避免 double free，这个动作叫 *move* 而不是浅拷贝。
//
// 原文报错（rustc 1.7x，路径与列号随文件位置变化）：
//   error[E0382]: borrow of moved value: `s1`
//    --> src/main.rs:5:16
//     |
//   2 |     let s1 = String::from("hello");
//     |         -- move occurs because `s1` has type `String`, which does not
//     |            implement the `Copy` trait
//   3 |     let s2 = s1;
//     |              -- value moved here
//   5 |     println!("{s1}, world!");
//     |                ^^ value borrowed here after move
//     |
//   help: consider cloning the value if the performance cost is acceptable
//
// 规避：需要两份数据就显式 .clone()（深拷贝堆数据）或改用 &s1 借用。

fn main() {
    let s1 = String::from("hello");
    let s2 = s1; // s1 被 move 进 s2

    println!("{s1}, world!"); // E0382
    println!("{s2}");
}
