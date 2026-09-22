// 过程宏**不卫生**：生成的代码在调用处解析。
// 若宏生成了 `Option` 这样的短名，调用方自己定义了一个 `Option` 就会撞上，
// 于是宏作者必须写绝对路径 `::std::option::Option`。
//
// 假设 mymacro 的某个过程宏生成了 `fn f() -> Option<u32> { None }`：
struct Option; // 调用方自己定义了一个叫 Option 的类型

include!("../generated_by_macro.rs"); // 展开后是 `fn f() -> Option<u32>`，
                                      // 这里的 `Option` 解析到上面这个 struct
                                      // → ERROR: 期望类型参数 / 不是泛型

fn main() {
    let _ = f();
}
