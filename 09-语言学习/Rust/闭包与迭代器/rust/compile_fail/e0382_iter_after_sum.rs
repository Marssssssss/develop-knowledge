//! error[E0382]: borrow of moved value
//! 官方 ch13-02："We aren't allowed to use v1_iter after the call to sum,
//! because sum takes ownership of the iterator we call it on."
//! 消费适配器（sum / collect / count / for_each）都拿走 self 的所有权。

fn main() {
    let v1 = vec![1, 2, 3];
    let v1_iter = v1.iter();
    let total: i32 = v1_iter.sum();
    println!("{total}");
    let again = v1_iter.next(); // <-- E0382: use of moved value
    println!("{again:?}");
}
