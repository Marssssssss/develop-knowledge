//! error[E0277]: `MutexGuard<'_, i32>` cannot be sent between threads safely
//!
//! std 明确写了 `impl<T: ?Sized> !Send for MutexGuard<'_, T>` —— 官方注释的理由是
//! "to prevent it being dropped from a different thread than it was locked in"
//! （某些平台的 pthread 要求解锁与加锁是同一线程）。
//! 但同一份文档也写了 `impl<T: Sync> Sync for MutexGuard<'_, T>`：
//! **&MutexGuard 可以跨线程，MutexGuard 本身不行**。

use std::sync::Mutex;
use std::thread;

fn main() {
    let m = Mutex::new(0);
    let guard = m.lock().unwrap();
    let h = thread::spawn(move || {
        //   ^^^^^^^^^^^^^^ E0277: `MutexGuard<'_, i32>` cannot be sent
        *guard += 1;
    });
    h.join().unwrap();
    println!("{}", *m.lock().unwrap());
}
