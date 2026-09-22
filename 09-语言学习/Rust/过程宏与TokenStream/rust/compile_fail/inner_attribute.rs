// 官方：属性宏**不能**用作 inner attribute（`#![...]`），
// 只能用在 Items / extern 块里的 item / 固有与 trait 实现 / trait 定义 上。
#![show_streams] // ERROR: 属性宏不能用于 crate 级 / inner attribute

fn main() {}
