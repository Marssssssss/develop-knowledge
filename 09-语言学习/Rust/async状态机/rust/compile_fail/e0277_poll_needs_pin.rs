//! error[E0599]: no method named `poll` found ... （签名不匹配）
//!
//! `Future::poll` 的接收者是 `Pin<&mut Self>`，不是 `&mut Self`。
//! 想 poll 一个 future，必须先把它钉住：`Box::pin(fut)` 或 `std::pin::pin!(fut)`。
//! 这条约束是**故意**的 —— 正因为 poll 拿不到所有权，自引用 future 才敢在自己的
//! 字段里存指向自己的裸指针（async-book 原文："it allows us to create futures
//! that are immovable"）。

use std::future::Future;
use std::task::{Context, Poll};

struct Plain(i32);

impl Future for Plain {
    type Output = i32;
    fn poll(&mut self, _cx: &mut Context<'_>) -> Poll<i32> {
        //  ^^^^^ 应当是 `self: Pin<&mut Self>` —— 签名不匹配，trait 未实现
        Poll::Ready(self.0)
    }
}

fn main() {
    let _ = Plain(1);
}
