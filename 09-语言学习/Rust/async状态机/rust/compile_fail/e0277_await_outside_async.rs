//! error[E0728]: `await` is only allowed inside `async` functions and blocks
//!
//! `.await` 不是普通的方法调用，它是**状态机的挂起点**：编译器要把 `async fn` 的
//! 函数体改写成一台状态机，每个 `.await` 都是一个可恢复的挂起位置。
//! 这个改写只在 async 上下文里发生，所以在普通 fn 里写 `.await` 直接报 E0728。

use std::future::Future;

fn poll_it<F: Future>(f: F) -> F::Output {
    f.await // <-- E0728
}

fn main() {
    let _ = poll_it(async { 1 });
}
