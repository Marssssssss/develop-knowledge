// 跟随集限制：`expr` 与 `stmt` 后面只能跟 `=>`、`,`、`;`。
// `[` 之所以被禁，不是因为今天的 Rust 里 `[,]` 有歧义，
// 而是因为 `[` 可能开启后缀表达式，将来会让这个 matcher 变得有歧义。
macro_rules! ambiguous {
    ($i:expr [ , ]) => { $i };
}

fn main() {
    let v = ambiguous!(1 [ , ]);
    println!("{:?}", v);
}
