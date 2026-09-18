//! error[E0277]: the `?` operator can only be used in a function that returns
//! `Result` or `Option` (or another type that implements `FromResidual`)
//!
//! `?` 的本质是「提前 return」，所以函数必须有能接收这个 residual 的返回类型。
//! 返回 `()` 的函数里写 `?` 就是这句话的反例。
//! （Reference 补充：真正决定能否用 `?` 的是 `Try` / `FromResidual`，
//!  而 `Try` trait 目前仍是 unstable，不能给自己类型实现。）

use std::fs::File;

fn read_it() {
    let _ = File::open("hello.txt")?; // <-- E0277
}

fn main() {
    read_it();
}
