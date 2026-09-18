//! error[E0502]: cannot borrow `v` as mutable because it is also borrowed as immutable
//!
//! 这是本 demo 最重要的一条反证：官方原话
//! "unsafe **doesn't turn off the borrow checker** or disable any of Rust's other
//! safety checks: If you use a reference in unsafe code, it will still be checked."
//!
//! `unsafe` 只解锁那**五项**能力；只要代码里出现的是普通引用，规则照旧。
//! 想绕过借用检查必须改用裸指针（并且那之后就是你自己保证不悬垂了）。

fn main() {
    let mut v = vec![1, 2, 3];
    let first = &v[0];
    unsafe {
        v.push(4); // <-- E0502：即使包在 unsafe 里也照样报错
    }
    println!("{first}");
}
