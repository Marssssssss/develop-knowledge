// 官方原文："The macros may not be used from the crate where they are defined,
// and can only be used when imported in another crate."
// 把这个文件放进 mymacro/src/ 下编译就会报「cannot be used from the crate
// where it is defined」——这正是过程宏要单独开一个 crate 的原因。
#[proc_macro]
pub fn make_answer(_item: TokenStream) -> TokenStream {
    "fn answer() -> u32 { 42 }".parse().unwrap()
}

fn main() {
    make_answer!(); // ERROR: 不能在定义它的 crate 里使用
}
