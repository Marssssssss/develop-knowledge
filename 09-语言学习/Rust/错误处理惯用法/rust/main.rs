//! Rust 错误处理惯用法：`Result` + `?` + `From` 自动转换 + `Box<dyn Error>` + source 链。
//!
//! 依据：
//!   * The Book ch09-02 —— `?` 走的 error 会经过 `From::from`；`Box<dyn Error>` = "any kind of error"。
//!   * Rust Reference —— `?` 还能作用于 Option / ControlFlow / Poll<Result<T,E>> /
//!     Poll<Option<Result<T,E>>>，去糖为 `Try::branch` + `FromResidual::from_residual`。
//!   * std::error::Error —— `pub trait Error: Debug + Display`；source() 用于跨抽象边界。
//!
//! 编译：cargo run            （正常演示，退出码 0）
//!      cargo run -- fail    （演示 Err 路径，退出码非 0）

use std::error::Error;
use std::fmt;
use std::fs::File;
use std::io::{self, Read};
use std::num::ParseIntError;
use std::ops::ControlFlow;

// ---------------------------------------------------------------- 自定义错误

#[derive(Debug)]
enum AppError {
    Io(io::Error),
    Parse(ParseIntError),
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AppError::Io(_) => write!(f, "unable to read configuration"),
            AppError::Parse(_) => write!(f, "config value is not a number"),
        }
    }
}

impl Error for AppError {
    /// std 原文：source() 用于「错误跨越抽象边界」时暴露下层原因。
    /// 注意：**不要**把 source 的文本再拼进 Display —— 官方明确说二者只能选一个。
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            AppError::Io(e) => Some(e),
            AppError::Parse(e) => Some(e),
        }
    }
}

// 这两条 From 就是 `?` 能跨层传播的全部秘密
impl From<io::Error> for AppError {
    fn from(e: io::Error) -> Self {
        AppError::Io(e)
    }
}
impl From<ParseIntError> for AppError {
    fn from(e: ParseIntError) -> Self {
        AppError::Parse(e)
    }
}

// ---------------------------------------------------------------- ? 链

/// 三个 `?`，三种来源，一个出口错误类型。
fn read_and_parse(path: &str) -> Result<u32, AppError> {
    let mut s = String::new();
    File::open(path)?.read_to_string(&mut s)?; // io::Error  → AppError
    let n: u32 = s.trim().parse()?; // ParseIntError → AppError
    Ok(n)
}

/// `?` 作用在 Option 上：None 直接返回，**不经过 From**（Reference 明确列出）。
fn first_upper(s: &str) -> Option<char> {
    let c = s.chars().next()?;
    Some(c.to_ascii_uppercase())
}

/// `?` 作用在 ControlFlow 上：Break 直接提前返回，同样不经过 From。
fn reject_negative(v: &[i32]) -> ControlFlow<&'static str> {
    for x in v {
        if *x < 0 {
            ControlFlow::Break("negative found")?;
        }
    }
    ControlFlow::Continue(())
}

// ---------------------------------------------------------------- 错误链打印

fn print_chain(top: &dyn Error) {
    let mut depth = 0;
    let mut cur: Option<&dyn Error> = Some(top);
    while let Some(e) = cur {
        println!("    [{depth}] {e}");
        cur = e.source();
        depth += 1;
    }
}

// ---------------------------------------------------------------- main

fn main() -> Result<(), Box<dyn Error>> {
    // ---- 1. 同一条 ? 链，两种失败
    println!("1) 文件不存在时：");
    match read_and_parse("does-not-exist.toml") {
        Ok(n) => println!("    parsed {n}"),
        Err(e) => print_chain(&e),
    }
    println!("   内容非数字时：");
    std::fs::write("bad-config.toml", "not-a-number")?;
    match read_and_parse("bad-config.toml") {
        Ok(n) => println!("    parsed {n}"),
        Err(e) => print_chain(&e),
    }

    // ---- 2. Box<dyn Error>：一个静态类型装下任意错误
    let a: Result<(), Box<dyn Error>> = Err(Box::new(io::Error::new(
        io::ErrorKind::NotFound,
        "no such file",
    )));
    let b: Result<(), Box<dyn Error>> = Err(Box::new("abc".parse::<u32>().unwrap_err()));
    println!("2) 同一类型装下两种错误: {} | {}", a.unwrap_err(), b.unwrap_err());

    // ---- 3. Option 与 ControlFlow 上的 ?
    println!("3) first_upper(\"\") = {:?}；first_upper(\"rust\") = {:?}",
        first_upper(""), first_upper("rust"));
    println!("   reject_negative([1,-2]) = {:?}", reject_negative(&[1, -2]));
    println!("   reject_negative([1,2])  = {:?}", reject_negative(&[1, 2]));

    // ---- 4. unwrap vs expect（官方：生产代码更倾向 expect）
    let good: Result<i32, &str> = Ok(7);
    println!("4) unwrap 成功: {}", good.unwrap());
    let parsed: Result<i32, _> = "42".parse::<i32>();
    println!("   expect 成功: {}", parsed.expect("\"42\" 一定能解析成 i32"));

    // ---- 5. 退出码：Ok → 0；Err → 非 0（兼容 C 约定）
    if std::env::args().any(|a| a == "fail") {
        return Err(Box::new(AppError::Io(io::Error::new(
            io::ErrorKind::NotFound,
            "config.toml",
        ))));
    }
    println!("5) 正常结束 → 退出码 0；`cargo run -- fail` 可看到退出码非 0");
    Ok(())
}
