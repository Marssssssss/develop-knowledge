//! error[E0507]: cannot move out of dereference of `Pin<Box<SelfRef>>`
//!
//! 这就是 Pin 的全部意义：把值钉住之后，**安全代码再也拿不出它的所有权**，
//! 于是它地址不变，自引用指针才能一直有效。
//! 除非 T: Unpin —— 那时编译器认为搬动无害，`DerefMut` 也可用。

use std::marker::PhantomPinned;
use std::pin::Pin;

struct SelfRef {
    a: i32,
    ptr_to_a: *const i32,
    _pin: PhantomPinned, // 摘掉自动 Unpin
}

fn main() {
    let boxed: Pin<Box<SelfRef>> = Box::pin(SelfRef {
        a: 42,
        ptr_to_a: std::ptr::null(),
        _pin: PhantomPinned,
    });
    let moved = *boxed; // <-- E0507: cannot move out of dereference of `Pin<Box<SelfRef>>`
    println!("{}", moved.a);
}
