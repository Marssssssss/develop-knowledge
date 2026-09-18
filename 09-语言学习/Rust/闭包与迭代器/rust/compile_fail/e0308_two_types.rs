//! error[E0308]: mismatched types
//! 官方 Listing 13-3：闭包的参数/返回类型由**第一次调用**推断并锁定，
//! 之后再用别的类型调就报错（不像泛型函数那样每次单态化出一份新的）。

fn main() {
    let example_closure = |x| x;
    let s = example_closure(String::from("hello")); // 推断为 String -> String
    let n = example_closure(5); // <-- E0308: expected `String`, found integer
    println!("{s} {n}");
}
