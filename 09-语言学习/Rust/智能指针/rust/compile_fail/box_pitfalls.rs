// Box<T> 支持 dyn 与递归，但「Box 本身不复制数据」这一点容易误解
//
// 本文件演示两种常见误用（各自单独观察）：
//
// (A) 用 Box 包装后仍按值拷贝
//     观察方式： rustc box_pitfalls.rs
//     报错要点： error[E0382]: use of moved value —— Box<T> 本身不是 Copy，
//               `let b2 = b1;` 是把 Box（含堆所有权）整体 move 走。
//
// (B) 想靠 Box 摆脱生命周期：Box 只是换了存放位置，不改变借用规则
//     `fn f<'a>(x: &'a str) -> Box<&'a str>` 是合法的；
//     但 `fn g() -> Box<&str>` 仍然报 E0106（missing lifetime specifier）。
//
// 要点：Box 解决的是**大小**（size 在编译期已知 = 一个指针）与**间接层**问题，
//       不是生命周期问题，也不是拷贝问题。需要共享/可改请看 Rc/RefCell。

fn main() {
    let b1 = Box::new(String::from("hello"));
    let b2 = b1; // move 整体所有权
    println!("{}", b2);
    // println!("{}", b1); // ← 打开这行就是 E0382
}
