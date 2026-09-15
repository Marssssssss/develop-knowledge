//! Move / Copy / Clone 与 drop 时机（Rust demo 2/5）
//!
//! 用 `PrintOnDrop` 把「析构发生的时机与顺序」变成可断言的日志。
//!
//! 依据：Rust Reference *Destructors*（drop scope 嵌套、逆声明序、字段声明序、
//! 数组首→尾、模式内逆序、参数最后 drop、赋值即 drop 旧值、部分 move 只 drop 剩余字段）
//! 与 The Rust Programming Language ch04-01（Copy 类型清单、Copy 与 Drop 互斥）。

use std::cell::RefCell;

thread_local! {
    /// drop 事件日志（thread_local 让 Drop impl 能记录事件而不占用 &mut 借用）
    static LOG: RefCell<Vec<&'static str>> = RefCell::new(Vec::new());
}

/// 析构时把标签写进日志
struct PrintOnDrop(&'static str);

impl Drop for PrintOnDrop {
    fn drop(&mut self) {
        LOG.with(|log| log.borrow_mut().push(self.0));
    }
}

/// 取走并清空日志（必须在目标作用域**结束之后**调用）
fn take_log() -> Vec<&'static str> {
    LOG.with(|log| log.borrow_mut().drain(..).collect())
}

// ---------------------------------------------------------------------------
// 场景 1：同一作用域内变量按「声明顺序的逆序」drop
// ---------------------------------------------------------------------------
fn sc_reverse_declaration() -> Vec<&'static str> {
    {
        let _a = PrintOnDrop("a");
        let _b = PrintOnDrop("b");
        let _c = PrintOnDrop("c");
    } // 离开块作用域 → 逆序：c, b, a
    take_log()
}

// ---------------------------------------------------------------------------
// 场景 2：同时离开多个作用域时「由内向外」
// ---------------------------------------------------------------------------
fn sc_inner_block_first() -> Vec<&'static str> {
    {
        let _declared_first = PrintOnDrop("outer-1");
        {
            let _declared_in_block = PrintOnDrop("inner");
        } // 内层块先结束
        let _declared_last = PrintOnDrop("outer-2");
    }
    take_log()
}

// ---------------------------------------------------------------------------
// 场景 3：赋值给已初始化的变量 → 旧值**立即** drop
// ---------------------------------------------------------------------------
fn sc_overwrite() -> Vec<&'static str> {
    {
        let mut overwritten = PrintOnDrop("旧值：被覆盖时立即 drop");
        overwritten = PrintOnDrop("新值：作用域结束时 drop");
    }
    take_log()
}

// ---------------------------------------------------------------------------
// 场景 4：move 不是 clone —— 值只 drop 一次
// ---------------------------------------------------------------------------
fn sc_move_drops_once() -> Vec<&'static str> {
    {
        let moved;
        moved = PrintOnDrop("move 不触发析构"); // 对未初始化变量赋值：不运行析构
        let _taken = moved; // 所有权转移，仍不析构
    } // _taken 离开作用域 → 唯一一次析构
    take_log()
}

// ---------------------------------------------------------------------------
// 场景 5：struct 字段按「声明顺序」drop（与变量逆序相反）
// ---------------------------------------------------------------------------
struct Triple {
    a: PrintOnDrop,
    b: PrintOnDrop,
    c: PrintOnDrop,
}

fn sc_struct_fields() -> Vec<&'static str> {
    {
        let _t = Triple {
            a: PrintOnDrop("field-a"),
            b: PrintOnDrop("field-b"),
            c: PrintOnDrop("field-c"),
        };
    }
    take_log()
}

// ---------------------------------------------------------------------------
// 场景 6：数组元素「首→尾」
// ---------------------------------------------------------------------------
fn sc_array_elements() -> Vec<&'static str> {
    {
        let _arr = [
            PrintOnDrop("elem-0"),
            PrintOnDrop("elem-1"),
            PrintOnDrop("elem-2"),
        ];
    }
    take_log()
}

// ---------------------------------------------------------------------------
// 场景 7：模式内的绑定按「模式内声明的逆序」drop
// ---------------------------------------------------------------------------
fn sc_tuple_pattern() -> Vec<&'static str> {
    {
        let (_pat_first, _pat_last) = (PrintOnDrop("pat-first"), PrintOnDrop("pat-last"));
    } // 逆序 → pat-last, pat-first
    take_log()
}

// ---------------------------------------------------------------------------
// 场景 8：mem::forget 阻止析构 + 部分 move 后只 drop 剩余字段
// ---------------------------------------------------------------------------
fn sc_forget_and_partial_move() -> Vec<&'static str> {
    {
        let forgotten = PrintOnDrop("forgotten：永不 drop");
        std::mem::forget(forgotten); // 泄漏是「内存安全」的，但对象再也不析构

        let partial = (PrintOnDrop("partial-0"), PrintOnDrop("partial-1"));
        std::mem::forget(partial.1); // 部分 move：只剩 partial.0 仍初始化
    } // 只 drop partial.0
    take_log()
}

// ---------------------------------------------------------------------------
// 场景 9：Copy —— 赋值不 move，原变量继续可用
// ---------------------------------------------------------------------------
fn copy_vs_clone() {
    let x = 5;
    let y = x;
    println!("  [Copy]     x={x} y={y}       赋值后 x 仍可用（i32 实现 Copy）");

    let t1 = (1_i32, 2_i32);
    let t2 = t1;
    println!("  [Copy]     t1={t1:?} t2={t2:?}   元组只含 Copy 元素 → 也是 Copy");

    let s1 = String::from("hello");
    let s2 = s1.clone();
    println!("  [Clone]    s1={s1} s2={s2}   clone 深拷贝堆数据，两个都可用");

    #[derive(Copy, Clone, Debug)]
    struct Config {
        port: u16,
        debug: bool,
    }
    let c1 = Config {
        port: 8080,
        debug: true,
    };
    let c2 = c1;
    println!("  [derive]   c1={c1:?} c2={c2:?}  只含标量字段 → 可 derive Copy");
    assert_eq!(c1.port, c2.port);
}

/// 要求 `T: Copy` 的泛型函数：非 Copy 类型根本传不进来（编译期拒绝）
fn duplicate<T: Copy>(v: T) -> (T, T) {
    (v, v)
}

fn main() {
    println!("=== drop 时机与顺序（Rust Reference · Destructors）===\n");

    let cases: Vec<(&str, Vec<&'static str>, Vec<&'static str>)> = vec![
        (
            "同一作用域：逆声明序",
            sc_reverse_declaration(),
            vec!["c", "b", "a"],
        ),
        (
            "离开多层作用域：由内向外",
            sc_inner_block_first(),
            vec!["inner", "outer-2", "outer-1"],
        ),
        (
            "覆盖赋值：旧值立即 drop",
            sc_overwrite(),
            vec!["旧值：被覆盖时立即 drop", "新值：作用域结束时 drop"],
        ),
        (
            "move 不触发析构（只 drop 一次）",
            sc_move_drops_once(),
            vec!["move 不触发析构"],
        ),
        (
            "struct 字段：声明顺序",
            sc_struct_fields(),
            vec!["field-a", "field-b", "field-c"],
        ),
        (
            "数组元素：首 → 尾",
            sc_array_elements(),
            vec!["elem-0", "elem-1", "elem-2"],
        ),
        (
            "模式内绑定：逆声明序",
            sc_tuple_pattern(),
            vec!["pat-last", "pat-first"],
        ),
        (
            "forget + 部分 move",
            sc_forget_and_partial_move(),
            vec!["partial-0"],
        ),
    ];

    let mut ok = 0usize;
    for (name, got, expect) in &cases {
        let pass = got == expect;
        if pass {
            ok += 1;
        }
        println!("[{}] {name}", if pass { "PASS" } else { "FAIL" });
        println!("        实际 drop 序 = {got:?}");
        println!("        期望 drop 序 = {expect:?}");
    }

    println!("\n=== Copy 与 Clone ===");
    copy_vs_clone();
    let (p, q) = duplicate(7_u8);
    println!("  [T: Copy]  duplicate(7u8) = ({p}, {q})   泛型约束 T: Copy 在编译期拦掉非 Copy 类型");

    println!("\n=== 函数参数的 drop 顺序（Reference 给出的示例顺序为 3 2 0 1，此处只观测不断言）===");
    patterns_in_parameters(
        (PrintOnDrop("param-0"), PrintOnDrop("param-1")),
        (PrintOnDrop("param-2"), PrintOnDrop("param-3")),
    );
    println!("  实测顺序 = {:?}", take_log());
    println!("  依据：所有函数参数属于「整个函数」作用域，因此最后 drop；");
    println!("        每个参数在其实参模式引入的绑定**之后** drop。");

    println!("\n断言：{ok}/{} 个 drop 顺序场景通过", cases.len());
    assert_eq!(ok, cases.len(), "drop 顺序与 Rust Reference 不符");
    println!("补充：Copy + Drop 互斥（E0184）无法运行期演示，见 compile_fail/。");
}

// 参数全部属于「整个函数」作用域 → 最后 drop；参数内绑定先于参数自身 drop
fn patterns_in_parameters(
    (_x, _): (PrintOnDrop, PrintOnDrop),
    (_, _y): (PrintOnDrop, PrintOnDrop),
) {
}
