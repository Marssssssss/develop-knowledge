// E0599：候选列表全部走完也没有同名方法。
// 注意区分：解引用只走 `Deref` 链，`String` 能找到 `str::len`
// 是因为 `str` 在链上；换成没有 Deref 的类型就找不到。
struct Point {
    x: i32,
}

fn main() {
    let p = Point { x: 1 };
    println!("{}", p.len()); // ERROR: no method named `len` found for struct `Point`
}
