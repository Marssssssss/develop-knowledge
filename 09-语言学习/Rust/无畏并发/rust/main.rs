//! ch16 无畏并发：Send / Sync 是**语言内置**的自动 trait，其余并发设施都在标准库里。
//!
//! 官方原文（ch16-04）："among the key concurrency concepts that are embedded in the
//! language rather than the standard library are the std::marker traits Send and Sync."
//!
//! 编译：cargo run

use std::sync::{Arc, Mutex};
use std::thread;

/// 一个只接受 T: Send 的函数：把值搬到新线程里跑完再拿回来。
/// 传不 Send 的类型会在**编译期**被拦下（E0277）。
fn run_in_thread<T>(value: T) -> T
where
    T: Send + 'static,
{
    thread::spawn(move || value).join().unwrap()
}

/// 只接受 T: Sync：把**同一个引用**交给两个线程同时读。
/// 用 thread::scope 是因为引用不是 'static，spawn 出来的线程不能比它活得久。
fn read_from_two_threads<T: Sync>(value: &T) {
    thread::scope(|s| {
        let h1 = s.spawn(|| std::ptr::addr_of!(*value) as usize);
        let h2 = s.spawn(|| std::ptr::addr_of!(*value) as usize);
        let (p1, p2) = (h1.join().unwrap(), h2.join().unwrap());
        assert_eq!(p1, p2); // 两个线程看到同一地址 —— 这正是 Sync 允许的事
    });
}

fn main() {
    // ---- 1. 官方 Listing 16-15：10 个线程自增共享计数器
    let counter = Arc::new(Mutex::new(0));
    let mut handles = vec![];
    for _ in 0..10 {
        let counter = Arc::clone(&counter);
        let handle = thread::spawn(move || {
            let mut num = counter.lock().unwrap(); // MutexGuard
            *num += 1;                             // Deref 到内部 i32
        });                                        // guard 在此 drop → 自动解锁
        handles.push(handle);
    }
    for h in handles {
        h.join().unwrap();
    }
    println!("1) Result: {}", *counter.lock().unwrap()); // 官方输出：Result: 10

    // ---- 2. Send：值可以搬进另一个线程
    let moved = run_in_thread(String::from("hello from another thread"));
    println!("2) {moved}");
    // let _ = run_in_thread(std::rc::Rc::new(1)); // E0277: Rc<i32> cannot be sent

    // ---- 3. Sync：同一个引用可以被两个线程共享
    let shared = Mutex::new(42);
    read_from_two_threads(&shared);
    println!("3) &Mutex<i32> 是 Sync，两个线程看到同一地址");
    // let cell = std::cell::RefCell::new(1);
    // read_from_two_threads(&cell); // E0277: RefCell<i32> 不是 Sync

    // ---- 4. Mutex 提供内部可变性：counter 是 immutable 的，但能改到里面
    let m = Mutex::new(5);
    {
        let mut num = m.lock().unwrap();
        *num = 6;
    } // guard 离开作用域 → Drop → 解锁
    println!("4) m = {m:?}");

    // ---- 5. 中毒（poisoning）：持锁线程 panic 后，锁再也拿不到 Ok
    let poisoned = Arc::new(Mutex::new(0));
    let p2 = Arc::clone(&poisoned);
    let panicked = thread::spawn(move || {
        let _guard = p2.lock().unwrap();
        panic!("boom while holding the lock");
    });
    let _ = panicked.join();
    match poisoned.lock() {
        Ok(_) => println!("5) 拿到锁"),
        Err(e) => println!("5) PoisonError: {e}"), // 官方：unwrap 会在此 panic
    }
    // 想继续用就得先 clear_poison：
    if poisoned.is_poisoned() {
        poisoned.clear_poison();
    }
    println!("   clear_poison 之后可再用: {}", *poisoned.lock().unwrap());

    // ---- 6. 只是数值就别上锁（官方：有比 Mutex 更简单的类型）
    use std::sync::atomic::{AtomicUsize, Ordering};
    let hits = AtomicUsize::new(0);
    thread::scope(|s| {
        for _ in 0..10 {
            s.spawn(|| {
                hits.fetch_add(1, Ordering::SeqCst);
            });
        }
    });
    println!("6) AtomicUsize = {}", hits.load(Ordering::SeqCst));
}
