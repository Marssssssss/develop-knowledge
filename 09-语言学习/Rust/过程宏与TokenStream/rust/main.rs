// 过程宏的**使用侧**：必须在另一个 crate 里 import 之后才能用。
//
// 官方的那句「procedural macros are unhygienic」在实践中意味着：
// 生成的代码若写 `Option`、`Result` 这类名字，会在**调用处**解析，
// 因此正式的过程宏都写绝对路径 `::std::option::Option`，
// 并把生成物命名成 `__internal_xxx` 这类不太可能撞名的名字。

use mymacro::{make_answer, show_streams, WithHelperAttr};

// 1) 函数式宏：调用被**整个替换**成它返回的 token
make_answer!();

// 2) derive 宏：输出被**追加**在被标注的 item 之后（同一模块/块内）
mod derived {
    #[derive(mymacro::AnswerFn)]
    pub struct Struct;
}

// 3) derive 的辅助属性只在「这个 item 上、且写在 derive 之后」以及
//    「字段 / 变体上」可见
#[derive(WithHelperAttr)]
struct WithHelper {
    #[helper]
    field: (),
}

// 4) 属性宏：attr = 属性名后的定界 token 树，item = 其余部分
//    （运行时在编译输出里能看到 `attr: "bar"` / `item: "fn invoke2() {}"`）
#[show_streams(bar)]
fn invoke2() {}

#[show_streams { delimiters }]
fn invoke4() {}

#[show_streams(multiple => tokens)]
fn invoke3() {}

#[show_streams]
fn invoke1() {}

fn main() {
    println!("answer = {}", answer());
    invoke1();
    invoke2();
    invoke3();
    invoke4();
    let _ = WithHelper { field: () };
    println!("derive 追加的函数：{}", derived::answer());

    // TokenStream 的两种 token 定义差异（详见 README 与 Python 模型）：
    //   声明宏：`+=` 一个 token、`'a` 一个 lifetime token、`-1` 是两个 token
    //   过程宏：`+=` 两个 Punct、`'a` 是 `'` + ident、`-1` 是**一个**字面量
}
