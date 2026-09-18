//! unsafe Rust 的边界：五件只有 unsafe 能做的事，以及为什么它**不等于**关掉安全检查。
//!
//! 依据 The Book ch20-01：
//!   * unsafe superpowers 只有五项：解引用裸指针 / 调 unsafe 函数或方法 /
//!     访问或修改可变静态变量 / 实现 unsafe trait / 访问 union 字段。
//!   * "unsafe **doesn't turn off the borrow checker** or disable any of Rust's other
//!     safety checks"
//!   * "To isolate unsafe code as much as possible, it's best to enclose such code
//!     within a **safe abstraction** and provide a safe API"
//!
//! 编译：cargo run

use std::cell::UnsafeCell;

// ---------------------------------------------------------------- 1. 裸指针

fn raw_pointer_basics() {
    let mut num = 5;
    // 创建裸指针是**安全**的（官方："We can create raw pointers in safe code"）。
    // 现代拼写是 `&raw const num` / `&raw mut num`（1.82+），这里用等价的转型写法。
    let r1: *const i32 = &num;
    let r2: *mut i32 = &mut num;
    println!("1) 裸指针地址: {:p} / {:p}", r1, r2);
    unsafe {
        println!("   解引用 r1 = {}", *r1); // 解引用就要 unsafe 了
        *r2 += 1;
    }
    println!("   写入后 num = {num}");
}

// ---------------------------------------------------------------- 2. UnsafeCell

/// 一个最小的 `Cell<T>`：展示所有内部可变性的底层都是 UnsafeCell。
struct MyCell<T> {
    value: UnsafeCell<T>,
}

// 注意：**没有** `unsafe impl Sync` —— UnsafeCell 不是 Sync，
// std 文档原话："UnsafeCell does nothing to avoid data races"。
impl<T: Copy> MyCell<T> {
    fn new(v: T) -> Self {
        MyCell { value: UnsafeCell::new(v) }
    }

    /// 通过 `&self` 改写内容 —— 这就是「内部可变性」。
    /// 官方：`UnsafeCell<T>` opts-out of the immutability guarantee for `&T`。
    fn set(&self, v: T) {
        unsafe {
            *self.value.get() = v;
        }
    }

    fn get(&self) -> T {
        unsafe { *self.value.get() }
    }
}

// ---------------------------------------------------------------- 3. safe 抽象

/// 标准库 `split_at_mut` 的教学复刻（The Book Listing 20-6）。
/// 用安全 Rust 写不出来：两个 `&mut` 指向同一块切片会被借用检查拦下。
fn split_at_mut(values: &mut [i32], mid: usize) -> (&mut [i32], &mut [i32]) {
    let len = values.len();
    let ptr = values.as_mut_ptr();
    assert!(mid <= len); // ← 整个安全边界就靠这一句：把 UB 变成 panic
    unsafe {
        (
            std::slice::from_raw_parts_mut(ptr, mid),
            std::slice::from_raw_parts_mut(ptr.add(mid), len - mid),
        )
    }
}

// ---------------------------------------------------------------- 4. unsafe fn / trait

/// 标记 trait：承诺「可以逐字节解释」。
/// SAFETY: 实现者必须是没有任何 padding / 无效位模式的 POD 类型。
unsafe trait Pod {}
unsafe impl Pod for u32 {}

/// SAFETY: `p` 必须指向至少 4 个可读字节。
unsafe fn read_u32_le(p: *const u8) -> u32 {
    u32::from_le_bytes([*p, *p.add(1), *p.add(2), *p.add(3)])
}

/// 安全封装：由调用点保证契约，于是外层调用者不必写 unsafe。
fn read_u32_le_checked(b: &[u8]) -> Result<u32, &'static str> {
    if b.len() < 4 {
        return Err("need at least 4 bytes");
    }
    Ok(unsafe { read_u32_le(b.as_ptr()) })
}

// ---------------------------------------------------------------- 5. 可变静态

static mut COUNTER: u32 = 0;

fn bump() -> u32 {
    unsafe {
        COUNTER += 1; // 访问可变静态变量是五项 superpower 之一
        COUNTER
    }
}

// ---------------------------------------------------------------- 6. union

#[repr(C)]
union Bits {
    as_u32: u32,
    as_f32: f32,
}

// ---------------------------------------------------------------- main

fn main() {
    raw_pointer_basics();

    // 2. UnsafeCell：&self 也能改
    let c = MyCell::new(1);
    c.set(42); // 注意接收者是 &self
    println!("2) MyCell: {} → {}", 1, c.get());

    // 3. safe 抽象：越界是 panic 而不是 UB
    let mut v = vec![1, 2, 3, 4, 5, 6];
    let (a, b) = split_at_mut(&mut v, 3);
    println!("3) split_at_mut(3) = {a:?} / {b:?}");
    let caught = std::panic::catch_unwind(|| {
        let mut w = vec![1, 2, 3];
        split_at_mut(&mut w, 99);
    });
    println!("   越界 → {:?}（panic，不是 UB）", caught.is_err());

    // 4. unsafe fn 只从安全包装里调用
    let bytes = [0x01, 0x02, 0x03, 0x04];
    println!("4) read_u32_le = {:#x}", read_u32_le_checked(&bytes).unwrap());
    println!("   字节不够 → {:?}", read_u32_le_checked(&[1, 2]));
    fn _assert_pod<T: Pod>() {}
    _assert_pod::<u32>();

    // 5. 可变静态
    println!("5) COUNTER: {} {}", bump(), bump());

    // 6. union 字段访问
    let u = Bits { as_u32: 0x3f80_0000 };
    println!("6) 0x3f80_0000 当作 f32 = {}", unsafe { u.as_f32 });

    // 7. unsafe 不关借用检查：这一段即使放进 unsafe 也编译不过
    //    （见 compile_fail/e0502_borrow_check_inside_unsafe.rs）
    let guarded = vec![1, 2, 3];
    let first = &guarded[0];
    println!("7) 借用检查照常工作：first = {first}");
}
