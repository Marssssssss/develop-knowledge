//! error[E0277]: the trait bound `AppError: From<ParseIntError>` is not satisfied
//!
//! `?` 不是「把错误原样往上扔」，它会对 Err 里的值调用 `From::from` 转成
//! **当前函数的返回类型**。少写一条 `impl From<...>`，编译期就拦下来。
//! 这也是 thiserror 的 `#[from]` 存在的理由 —— 它帮你生成这条 impl
//! （并且隐含 `#[source]`，不必两个属性都写）。

use std::fs::File;
use std::io;
use std::num::ParseIntError;

#[derive(Debug)]
enum AppError {
    Io(io::Error),
    Parse(ParseIntError),
}

impl From<io::Error> for AppError {
    fn from(e: io::Error) -> Self {
        AppError::Io(e)
    }
}
// 故意缺失：impl From<ParseIntError> for AppError

fn read_and_parse() -> Result<u32, AppError> {
    let mut s = String::new();
    File::open("c.toml")?.read_to_string(&mut s)?;
    let n: u32 = s.trim().parse()?; // <-- E0277: AppError: From<ParseIntError> 未满足
    Ok(n)
}

fn main() {
    let _ = read_and_parse();
}
