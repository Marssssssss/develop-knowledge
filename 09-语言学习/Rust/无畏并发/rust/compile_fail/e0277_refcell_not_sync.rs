//! error[E0277]: `RefCell<i32>` cannot be shared between threads safely
//!
//! 这一条是很多人踩的坑：RefCell<T> **是 Send**（可以整个搬走），
//! 但 **不是 Sync**（运行期借用检查本身不是线程安全的）。
//! 于是 `Arc<RefCell<i32>>` 连 Send 都不是 —— 因为 Arc<T>: Send 要求 T: Send + Sync。
//! 想共享可变状态，必须把 RefCell 换成 Mutex（或 RwLock）。

use std::cell::RefCell;
use std::sync::Arc;
use std::thread;

fn main() {
    let counter = Arc::new(RefCell::new(0));
    let c2 = Arc::clone(&counter);
    let h = thread::spawn(move || {
        //   ^^^^^^^^^^^^^^ E0277: `RefCell<i32>` cannot be shared between
        //                  threads safely（Arc 要求 T: Send + Sync）
        *c2.borrow_mut() += 1;
    });
    h.join().unwrap();
    println!("{}", counter.borrow());
}
