// 转发（forwarding）：把已匹配的片段交给第二个宏时，后者看到的是**不透明 AST**，
// 无法用字面 token 去匹配它。只有 ident / lifetime / tt 例外。
macro_rules! foo {
    ($l:expr) => { bar!($l); }; // ERROR: no rules expected this token in macro call
}

macro_rules! bar {
    (3) => { 3 };
}

fn main() {
    println!("{}", foo!(3));
}
