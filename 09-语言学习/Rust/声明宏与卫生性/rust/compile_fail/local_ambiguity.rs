// 官方原文：matcher 匹配时**不向前看**（no lookahead）。
// 编译器不会多读一个 token 去确认后面是不是 `)`，于是直接报 local ambiguity。
macro_rules! ambiguity {
    ($($i:ident)* $j:ident) => { };
}

fn main() {
    ambiguity!(error); // ERROR: local ambiguity when calling macro `ambiguity`
}
