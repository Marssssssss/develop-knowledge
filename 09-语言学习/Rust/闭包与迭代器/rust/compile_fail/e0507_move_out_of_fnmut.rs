//! error[E0507]: cannot move out of `value`, a captured variable in an `FnMut` closure
//! 官方 Listing 13-8：想用「把 String push 进 vec」来数 sort_by_key 调用次数。
//! 因为 value 被**移出**闭包，这个闭包只剩 FnOnce；而 sort_by_key 的约束是
//! FnMut（它要对每个元素调用一次），于是编译失败。

#[derive(Debug)]
struct Rectangle {
    width: u32,
    height: u32,
}

fn main() {
    let mut list = [
        Rectangle { width: 10, height: 1 },
        Rectangle { width: 3, height: 5 },
    ];
    let mut sort_operations = vec![];
    let value = String::from("closure called");
    list.sort_by_key(|r| {
        sort_operations.push(value); // <-- E0507：value 被移出
        r.width
    });
    println!("{list:#?}");
}
