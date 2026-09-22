// 声明宏 macro_rules!：片段分类符 / 跟随集 / 重复 / 混合点卫生性 / 作用域
// 依据 Rust Reference `macros-by-example`。

// ---------------------------------------------------------------- 1. 片段分类符
// 15 种：block expr expr_2021 ident item lifetime literal meta pat pat_param
//        path stmt tt ty vis
macro_rules! kinds {
    ($e:expr, $i:ident, $t:ty, $l:literal, $p:pat, $k:tt) => {
        concat!("expr=", stringify!($e), " ident=", stringify!($i),
                " ty=", stringify!($t), " literal=", stringify!($l),
                " pat=", stringify!($p), " tt=", stringify!($k))
    };
}

// ---------------------------------------------------------------- 2. 跟随集
// `expr` / `stmt` 后面只能跟 `=>`、`,`、`;` —— 这是为了让未来的语法扩展
// 不会让今天的宏突然产生歧义。下面这种写法是合法的（逗号分隔）。
macro_rules! sum_exprs {
    ($first:expr, $($rest:expr),*) => { $first $(+ $rest)* };
    ($only:expr) => { $only };
}

// ---------------------------------------------------------------- 3. 重复
// `$( ... )sep rep`，rep 为 `*` / `+` / `?`；`?` 不能带分隔符。
macro_rules! pairs {
    ($($k:expr => $v:expr),* $(,)?) => {{
        let mut out = Vec::new();
        $( out.push(($k, $v)); )*
        out
    }};
}

// 转录里的重复必须与匹配里层数一致：下面把逗号分隔改成箭头串联。
macro_rules! join_with_arrow {
    ($($i:ident),*) => { stringify!($($i)->*) };
}

// ---------------------------------------------------------------- 4. 混合点卫生性
// 局部变量与标签在**定义处**查找，其它符号在**调用处**查找。
fn func() {
    println!("  call-site func");
}

macro_rules! check_hygiene {
    () => {{
        let x = 1;                 // 这个 x 属于宏的**定义处**
        assert_eq!(x, 1);          // 因此拿到的是上面的 1，而不是调用处的 99
        func();                    // func 在调用处查找
    }};
}

// 宏可以递归展开（名字在调用处查找，所以定义里能引用自己）
macro_rules! count_exprs {
    () => { 0 };
    ($head:expr $(, $tail:expr)*) => { 1 + count_exprs!($($tail),*) };
}

// ---------------------------------------------------------------- 5. $crate
// 指代「定义这个宏的 crate」，但不改变可见性规则。
pub mod inner {
    #[macro_export]
    macro_rules! call_foo {
        () => { $crate::inner::foo() };
    }
    pub fn foo() -> &'static str { "inner::foo" }
}

// ---------------------------------------------------------------- 6. 文本作用域
// 宏在进入作用域后即可用，且能进入子模块；后定义的遮蔽先定义的。
macro_rules! greet {
    () => { "first" };
}

fn use_first() -> &'static str { greet!() }

mod child {
    // 父模块定义的宏在子模块里依然可见（textual scope 可跨模块）
    pub fn from_parent() -> &'static str { greet!() }
}

fn main() {
    println!("1. {}", kinds!(1 + 2, myvar, i32, 42, Some(x), [any tokens]));
    println!("2. sum = {}", sum_exprs!(1, 2, 3));
    println!("3. pairs = {:?}", pairs!("a" => 1, "b" => 2,));
    println!("3. joined = {}", join_with_arrow!(alpha, beta, gamma));
    println!("4. hygiene:");
    {
        let x = 99;            // 调用处的 x 不会被宏看到
        check_hygiene!();
        let _ = x;
    }
    println!("5. count = {}", count_exprs!(1, 2, 3));
    println!("6. {}", inner::foo());
    println!("7. first={} child={}", use_first(), child::from_parent());

    // 文本作用域：后定义者遮蔽先定义者
    macro_rules! greet { () => { "second" } }
    println!("7. shadowed = {}", greet!());
}
