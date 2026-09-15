// E0597: `x` does not live long enough（被引用的数据活得比引用短）
//
// 观察方式： rustc e0597_does_not_live_long_enough.rs
// 依据：The Book ch10-03 —— "Rust compares the size of the two lifetimes and sees that
//       `r` has a lifetime of `'a` but that it refers to memory with a lifetime of `'b`.
//       The program is rejected because `'b` is shorter than `'a`: The subject of the
//       reference doesn't live as long as the reference."
//
// 原文报错：
//   error[E0597]: `x` does not live long enough
//    --> src/main.rs:6:13
//     |
//   5 |         let x = 5;
//     |             - binding `x` declared here
//   6 |         r = &x;
//     |             ^^ borrowed value does not live long enough
//   7 |     }
//     |     - `x` dropped here while still borrowed
//   9 |     println!("r: {r}");
//     |                   - borrow later used here
//
// 语义：Rust 会保证「若存在对某数据的引用，该数据不会先于这个引用离开作用域」；
//       这里是外层变量 r 想活到 x 之后，故被拒绝。

fn main() {
    let r; // ----------+-- 'a
    {
        //           |
        let x = 5; // -+-- 'b  |
        r = &x; //    |       |
    } //  -+       |
    println!("r: {r}"); //           |
} // ----------+
