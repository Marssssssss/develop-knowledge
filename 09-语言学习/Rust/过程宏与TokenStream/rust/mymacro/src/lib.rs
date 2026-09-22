// 三类过程宏：**必须**定义在 crate 根部，且**不能**在定义它的 crate 里使用。
// 它们操作的是 `TokenStream`（≈ `Vec<TokenTree>`），不是 AST，
// 且官方明说 **procedural macros are unhygienic**。

extern crate proc_macro;

use proc_macro::TokenStream;

// ---------------------------------------------------------------- 1. 函数式宏
// `custom!(...)`：把定界符里的内容作为输入，**替换**整个调用。
#[proc_macro]
pub fn make_answer(_item: TokenStream) -> TokenStream {
    "fn answer() -> u32 { 42 }".parse().unwrap()
}

// ---------------------------------------------------------------- 2. derive 宏
// 输入是 struct/enum/union 的 token stream；输出是**追加**在它后面的若干 item。
#[proc_macro_derive(AnswerFn)]
pub fn derive_answer_fn(_item: TokenStream) -> TokenStream {
    "fn answer() -> u32 { 42 }".parse().unwrap()
}

// derive 宏可以用 `attributes(..)` 声明**辅助属性**（inert），
// 它们只在「应用该 derive 的 item」范围内可见，但任何宏都能看见它们。
#[proc_macro_derive(WithHelperAttr, attributes(helper))]
pub fn derive_with_helper_attr(_item: TokenStream) -> TokenStream {
    TokenStream::new()
}

// ---------------------------------------------------------------- 3. 属性宏
// 第一个 TokenStream 是属性名之后的定界 token 树（不含外层定界符），
// 第二个是 item 的其余部分（含 item 上的其它属性）；
// 返回的 0..n 个 item **替换**原来的 item。
#[proc_macro_attribute]
pub fn show_streams(attr: TokenStream, item: TokenStream) -> TokenStream {
    println!("attr: \"{attr}\"");
    println!("item: \"{item}\"");
    item // 原样返回 = 恒等属性
}

// ---------------------------------------------------------------- 4. 报错方式
// 官方给了两种：panic（被编译器捕获成编译错误），或 emit 一个 compile_error!。
#[proc_macro]
pub fn needs_name(item: TokenStream) -> TokenStream {
    if item.is_empty() {
        // 编译期报错：指向调用点，而不是宏的定义处
        return r#"compile_error!("needs_name! 需要一个标识符");"#
            .parse()
            .unwrap();
    }
    let name = item.to_string();
    format!("fn {}() -> u32 {{ 7 }}", name).parse().unwrap()
}
