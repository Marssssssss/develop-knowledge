// E0184: the trait `Copy` may not be implemented for this type; the type has a destructor
//
// 观察方式： rustc e0184_copy_with_drop.rs
// 依据：The Book ch04-01 —— "Rust won't let us annotate a type with `Copy` if the type,
//       or any of its parts, has implemented the `Drop` trait. If the type needs
//       something special to happen when the value goes out of scope and we add the
//       `Copy` annotation to that type, we'll get a compile-time error."
//
// 原文报错：
//   error[E0184]: the trait `Copy` may not be implemented for this type; the type
//                 has a destructor
//    --> src/main.rs:1:10
//     |
//   1 | #[derive(Copy, Clone)]
//     |          ^^^^ Copy not allowed on types with destructors
//     |
//     = note: this error originates in the derive macro `Copy` (in Nightly builds,
//             run with -Z macro-backtrace for more info)
//
// 为什么互斥：Copy 的含义是「可以用 memcpy 平拷贝」，一旦平拷贝出两份、
// 两份都执行析构，就会 double free / 重复关闭 fd。故编译器直接禁止这个组合。
//
// 规避：把资源型类型保持为 move 语义；需要共享时用 Rc<T> / Arc<T>。

#[derive(Copy, Clone)]
struct Handle {
    fd: i32,
}

impl Drop for Handle {
    fn drop(&mut self) {
        // 真实实现里这里会 close(self.fd)
        println!("closing fd {}", self.fd);
    }
}

fn main() {
    let h = Handle { fd: 3 };
    let h2 = h;
    println!("{} {}", h.fd, h2.fd);
}
