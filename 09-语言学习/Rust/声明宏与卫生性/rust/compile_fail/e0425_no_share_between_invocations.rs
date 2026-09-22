// 宏展开里定义的局部变量 / 标签**不在不同次调用之间共享**，
// 这是 mixed-site hygiene 的直接后果。
macro_rules! m {
    (define) => { let x = 1; };
    (refer) => { dbg!(x); };
}

fn main() {
    m!(define);
    m!(refer); // ERROR E0425: cannot find value `x` in this scope
}
