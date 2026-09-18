//! error[E0133]: use of mutable static is unsafe and requires unsafe function or block
//!
//! 可变静态变量是全局可写的共享状态，两个线程同时改就是数据竞争，
//! 而编译器无法为「谁在什么时候访问它」建立任何局部性论证 ——
//! 所以访问/修改它被列为五项 unsafe superpower 之一。

static mut HITS: u32 = 0;

fn main() {
    HITS += 1; // <-- E0133
    println!("{HITS}");
}
