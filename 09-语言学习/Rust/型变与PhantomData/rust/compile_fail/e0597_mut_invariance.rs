// &mut T 在 T 上**不变**，这正是它必须不变的原因：
// 若 &mut &'static str 能接受一个短命的 &str，就会把短引用写进本该只装
// 'static 的位置，制造悬垂。
fn shorten(r: &mut &'static str) {
    let s = String::from("short-lived");
    *r = &s; // ERROR: `s` does not live long enough
}

fn main() {
    let mut target: &'static str = "long";
    shorten(&mut target);
    println!("{}", target);
}
