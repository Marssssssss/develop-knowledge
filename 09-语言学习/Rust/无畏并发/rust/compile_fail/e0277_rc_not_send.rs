//! error[E0277]: `Rc<Mutex<i32>>` cannot be sent between threads safely
//! 官方 Listing 16-14。Rc 用**非原子**增减计数：两个线程同时 clone 指向同一值的 Rc，
//! 会并发修改计数 → 未定义行为。所以 Rc 既不 Send 也不 Sync。

use std::rc::Rc;
use std::sync::Mutex;
use std::thread;

fn main() {
    let counter = Rc::new(Mutex::new(0));
    let mut handles = vec![];
    for _ in 0..10 {
        let counter = Rc::clone(&counter);
        let handle = thread::spawn(move || {
            //        ^^^^^^^^^^^^^^ E0277: the trait `Send` is not implemented
            //                       for `Rc<Mutex<i32>>`
            let mut num = counter.lock().unwrap();
            *num += 1;
        });
        handles.push(handle);
    }
    for h in handles {
        h.join().unwrap();
    }
    println!("Result: {}", *counter.lock().unwrap());
}
