// E0392：参数 `'a` / `T` 从未被使用 —— 裸指针既不携带生命周期也不表达所有权。
// 解法：加一个 `PhantomData<&'a T>`（或其它形态，见 main.rs）。
struct Slice<'a, T> {
    start: *const T,
    end: *const T,
}

fn main() {
    let _ = std::mem::size_of::<Slice<u8>>();
    // ERROR: parameter `'a` is never used / parameter `T` is never used
}
