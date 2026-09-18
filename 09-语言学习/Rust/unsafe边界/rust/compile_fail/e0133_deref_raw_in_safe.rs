//! error[E0133]: dereference of raw pointer is unsafe and requires unsafe function or block
//!
//! 官方：裸指针"are allowed to ignore the borrowing rules … Aren't guaranteed to point
//! to valid memory … Are allowed to be null … Don't implement any automatic cleanup"。
//! **创建**它们是安全的，代价全部押在**解引用**上 —— 所以只有解引用被 E0133 拦住。

fn main() {
    let num = 5;
    let r: *const i32 = &num; // 安全：只是造一个指针
    println!("{}", *r); // <-- E0133
}
