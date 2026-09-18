//! error[E0512]: cannot transmute between types of different sizes
//!
//! Rustonomicon 原文："`mem::transmute<T, U>` takes a value of type T and reinterprets
//! it to have type U. **The only restriction is that the T and U are verified to have
//! the same size.**"
//!
//! 也就是说：尺寸检查是 `transmute` 唯一的编译期保护。尺寸相同之后发生的一切
//! （把 `&` 转成 `&mut`、造出非法值、依赖 `repr(Rust)` 的布局）**全都是 UB
//! 且编译器一声不响** —— 这类错误只能靠人审。

fn main() {
    let x: u32 = 7;
    let y: u64 = unsafe { std::mem::transmute(x) }; // <-- E0512: 4 bytes to 8 bytes
    println!("{y}");
}
