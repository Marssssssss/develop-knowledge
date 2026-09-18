//! error[E0277]: `MyError` doesn't implement `std::fmt::Display`
//!
//! std 的定义是 `pub trait Error: Debug + Display` —— **两个都必须有**。
//! 官方原文："Errors must describe themselves through the Display and Debug traits."
//! Error messages 的惯例是「小写短句、不带尾部句号」，
//! 因为调用方常把它拼进更大的上下文里（anyhow 的 `Caused by:` 就是这么拼的）。

use std::error::Error;
use std::fmt;

#[derive(Debug)]
struct MyError {
    detail: String,
}

impl Error for MyError {} // <-- E0277: MyError doesn't implement Display

fn main() {
    let e = MyError { detail: String::from("boom") };
    println!("{:?}", e);
    let _: &dyn fmt::Debug = &e;
}
