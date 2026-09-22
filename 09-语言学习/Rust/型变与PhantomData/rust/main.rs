// 型变（variance）与 PhantomData
// Reference：子类型化只发生在「生命周期」与「高阶生命周期」两处；
//           复合类型的型变由字段推导，同一参数出现在冲突位置 → invariant；
//           不在 struct 里时，每个位置**各自**计算。

use std::cell::Cell;
use std::marker::PhantomData;

// ---------------------------------------------------------------- 1. 生命周期协变
// &'static str 是 &'a str 的子类型，因为 'static outlives 'a。
fn shrink<'a>(s: &'static str) -> &'a str {
    s
}

// ---------------------------------------------------------------- 2. 官方的 struct 例子
// struct Variance<'a, 'b, 'c, T, U: 'a> {
//     x: &'a U,               // 协变 'a / 协变 U
//     y: *const T,            // 协变 T
//     z: UnsafeCell<&'b f64>, // 不变 'b
//     w: *mut U,              // 不变 U（与 x 冲突，最终 U 不变）
//     f: fn(&'c ()) -> &'c () // 既协又逆 → 不变 'c
// }
struct Variance<'a, 'b, 'c, T, U: 'a> {
    x: &'a U,
    y: *const T,
    z: std::cell::UnsafeCell<&'b f64>,
    w: *mut U,
    f: fn(&'c ()) -> &'c (),
}

// ---------------------------------------------------------------- 3. 逐位置计算
// 元组不是 struct，型变在每个位置上单独算：
// 'long 可以在协变位置收缩到 'short，同时不变位置保持 'long。
fn generic_tuple<'short, 'long: 'short>(
    x: (&'long u32, std::cell::UnsafeCell<&'long u32>),
) {
    let _: (&'short u32, std::cell::UnsafeCell<&'long u32>) = x;
}

// 函数指针同理：参数位可以**延长**到 'static，返回位可以**收缩**到 'short。
fn takes_fn_ptr<'short, 'middle: 'short>(f: fn(&'middle ()) -> &'middle ()) {
    let _: fn(&'static ()) -> &'short () = f;
}

// ---------------------------------------------------------------- 4. PhantomData
// 裸指针不表达所有权，也不携带生命周期；'a 与 T 都「没被用到」。
// 编译期会报 E0392（见 compile_fail/e0392_unused_param.rs），
// 解法就是 PhantomData：它只影响型变 / 自动 trait / drop check，不占空间。
struct Slice<'a, T> {
    start: *const T,
    end: *const T,
    _marker: PhantomData<&'a T>,   // 协变 'a、协变 T，Send+Sync 需 T: Sync
}

// 想让 T 不变、且不要求 T: Sync，就换成 *const T
struct SliceInvariant<'a, T> {
    start: *const T,
    end: *const T,
    _marker: PhantomData<&'a mut T>, // 协变 'a、不变 T、Send/Sync 继承
}

impl<'a, T> Slice<'a, T> {
    fn len(&self) -> usize {
        (self.end as usize - self.start as usize) / std::mem::size_of::<T>()
    }
}

// ---------------------------------------------------------------- 5. 高阶生命周期
// for<'a> F<'a> 是「把 'a 换成任意具体生命周期后」的类型的子类型。
fn hrtb() {
    let f: for<'a> fn(&'a i32) -> &'a i32 = |x| x;
    let g: fn(&'static i32) -> &'static i32 = f;   // 'a 被替换为 'static
    let _ = g;
}

// ---------------------------------------------------------------- 6. Cell 让生命周期不变
fn cell_invariant<'a>(c: &Cell<&'a ()>) -> &Cell<&'a ()> {
    c
}

// ---------------------------------------------------------------- 7. Unpin 的例外
// 想去掉 Unpin 要用专门的 PhantomPinned，PhantomData 做不到。
use std::marker::PhantomPinned;
struct NoMove {
    _pin: PhantomPinned,
}

fn main() {
    println!("{}", shrink("hello"));
    hrtb();
    let unit = ();
    let _ = cell_invariant(&Cell::new(&unit));
    let _ = std::mem::size_of::<NoMove>();
    println!("size_of::<Slice<u8>> = {}", std::mem::size_of::<Slice<u8>>());
    println!("size_of::<PhantomData<u8>> = {}",
             std::mem::size_of::<PhantomData<u8>>());
    let arr = [1u32, 2, 3];
    let s = Slice { start: arr.as_ptr(), end: unsafe { arr.as_ptr().add(3) },
                    _marker: PhantomData };
    println!("len = {}", s.len());
    let _: &SliceInvariant<u32> = &SliceInvariant {
        start: arr.as_ptr(), end: unsafe { arr.as_ptr().add(3) },
        _marker: PhantomData,
    };
}
