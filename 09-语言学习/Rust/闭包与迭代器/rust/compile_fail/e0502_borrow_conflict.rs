//! error[E0502]: cannot borrow `list` as immutable because it is also borrowed as mutable
//! 官方 Listing 13-5 的注释：可变借用期间不能再有共享借用。
//! 这说明「闭包的捕获发生在**定义时**，而不是调用时」。

fn main() {
    let mut list = vec![1, 2, 3];
    let mut borrows_mutably = || list.push(7); // 定义即捕获 &mut list
    println!("Before calling closure: {list:?}"); // <-- E0502
    borrows_mutably();
    println!("After calling closure: {list:?}");
}
