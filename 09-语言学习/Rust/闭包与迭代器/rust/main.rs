//! ch13 闭包与迭代器：Fn / FnMut / FnOnce 的推导、捕获方式、迭代器惰性。
//!
//! 三组 trait 的关系是**加法式**的（官方原文 "in an additive fashion"）：
//!   Fn ⊂ FnMut ⊂ FnOnce
//! 因此约束写成 FnOnce 的函数最宽容（三种闭包都能进），写成 Fn 的最严格。

use std::thread;

/// 约束为 FnOnce：只调用一次。对应官方 `Option::unwrap_or_else` 的签名。
fn call_once<F, T>(f: F) -> T
where
    F: FnOnce() -> T,
{
    f()
}

/// 约束为 FnMut：需要 `&mut F` 才能调用。对应 `slice::sort_by_key` 的签名。
fn call_via_mut_ref<F: FnMut(i32) -> i32>(f: &mut F) -> i32 {
    f(1) + f(2)
}

/// 约束为 Fn：`&F` 就够调用 —— 这是 Fn 与 FnMut 的分界线。
fn call_via_shared_ref<F: Fn(i32) -> i32>(f: &F) -> i32 {
    f(1) + f(2)
}

fn main() {
    // ---- 1. 只读捕获 → &T → Fn
    let list = vec![1, 2, 3];
    let only_borrows = || println!("1) from closure: {list:?}");
    only_borrows();
    println!("   list 仍可用: {list:?}"); // 共享借用不排斥读取

    let scale = |x: i32| x * 10;
    println!("   通过 &F 调用（说明它是 Fn）: {}", call_via_shared_ref(&scale));

    // ---- 2. 改动捕获 → &mut T → FnMut
    let mut list2 = vec![1, 2, 3];
    let mut push7 = || list2.push(7);
    push7();
    println!("2) after push: {list2:?}");

    let mut n = 0;
    let mut count = |x: i32| {
        n += 1;
        x
    };
    println!("   通过 &mut F 调用（说明它是 FnMut）: {}", call_via_mut_ref(&mut count));
    println!("   闭包被调用了 {n} 次");

    // ---- 3. 把捕获值移出 → 只有 FnOnce
    let value = String::from("closure called");
    let consumes = || value; // value 的所有权进入闭包，调用时再交出来
    println!("3) FnOnce: {}", call_once(consumes));
    // println!("{value}"); // E0382: value 已被移入闭包

    // ---- 4. move 关键字：强制按值捕获（thread::spawn 需要 'static + Send）
    let list3 = vec![1, 2, 3];
    let handle = thread::spawn(move || println!("4) from thread: {list3:?}"));
    handle.join().unwrap();

    // ---- 5. 迭代器是惰性的：不消费就一次都不跑
    let v1: Vec<i32> = vec![1, 2, 3];
    let mapped = v1.iter().map(|x| x + 1);
    println!("5) map 已构造，但闭包尚未运行");
    let collected: Vec<i32> = mapped.collect(); // 消费适配器在这里驱动一切
    println!("   collect() 之后: {collected:?}");

    let v1_iter = v1.iter();
    let total: i32 = v1_iter.sum();
    println!("   sum = {total}");
    // v1_iter.next(); // E0382: sum 已拿走迭代器所有权

    // ---- 6. 零成本抽象的结构面：链式 = 单趟 + 一次分配
    let v2: Vec<i32> = (1..=10).collect();
    let chained: Vec<i32> = v2.iter().filter(|x| **x % 2 == 0).map(|x| *x * 10).collect();

    let mut manual = Vec::new(); // 手写循环：同样是单趟
    for x in &v2 {
        if x % 2 == 0 {
            manual.push(x * 10);
        }
    }
    assert_eq!(chained, manual);
    println!("6) 链式 {chained:?} == 手写循环 {manual:?}");

    // ---- 7. take 的短路：上游不会走完
    let taken: Vec<i32> = (1..=100).take(3).collect();
    assert_eq!(taken, vec![1, 2, 3]);
    println!("7) (1..=100).take(3) = {taken:?}");

    // ---- 8. for 循环的去糖形态
    let mut it = v2.iter();
    let mut acc = 0;
    while let Some(x) = it.next() {
        acc += *x;
    }
    assert_eq!(acc, 55);
    println!("8) while let Some(x) = it.next() 累加 = {acc}");
}
