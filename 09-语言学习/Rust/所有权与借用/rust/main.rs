//! 所有权与借用检查（Rust demo 1/5）
//!
//! rustc 的借用检查发生在**编译期**，违反规则的片段根本无法编译进同一个 crate。
//! 因此本 demo 把 5 类报错的判定规则抽成一个**可在运行期执行的借用检查器模型**：
//! 输入是抽象的「语句序列」，输出是与 rustc 一致的错误码。
//! 非法片段的真实源码放在 `compile_fail/`，可逐个用 `rustc` 观察原文报错。
//! 依据：The Rust Programming Language ch04-01、ch04-02。

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum LoanKind {
    Shared, // &T
    Mut,    // &mut T
}

impl LoanKind {
    fn name(self) -> &'static str {
        match self {
            LoanKind::Shared => "&",
            LoanKind::Mut => "&mut",
        }
    }
}

/// 抽象语句：只保留与所有权/借用判定相关的动作
#[derive(Clone, Copy, Debug)]
enum Step {
    /// `let v = <owned>;` 或重赋值 —— 该变量此刻持有所有权
    Declare(&'static str),
    /// `let r = &v;` / `let r = &mut v;`
    Borrow(&'static str, &'static str, LoanKind),
    /// 读 `v` 或 `r`
    Use(&'static str),
    /// 把 `v` 的所有权移走（赋给别的变量 / 传参 / 返回）→ 之后 v 失效
    MoveOut(&'static str),
    /// 通过引用改数据（`r.push_str(..)` / `*r = ..`）
    Mutate(&'static str),
    /// 返回指向函数内局部变量的引用（无输入生命周期可借）
    ReturnLocalRef(&'static str),
}

struct Finding {
    code: &'static str,
    at: usize,
    what: String,
}

fn push_unique(f: &mut Vec<Finding>, code: &'static str, at: usize, what: String) {
    if !f.iter().any(|x| x.code == code && x.at == at) {
        f.push(Finding { code, at, what });
    }
}

#[derive(Clone, Copy)]
struct Loan {
    borrower: &'static str,
    target: &'static str,
    kind: LoanKind,
    start: usize,
}

/// NLL 语义：借用的“有效期”从创建处开始，到**该引用的最后一次使用**为止
/// （The Book ch04-02: "a reference's scope starts from where it is introduced and
/// continues through the last time that reference is used"）。
/// 从未被使用的引用其区间为空，不参与任何冲突。
fn last_use(steps: &[Step], borrower: &'static str) -> Option<usize> {
    steps.iter().enumerate().rev().find_map(|(i, s)| match s {
        Step::Use(x) | Step::Mutate(x) if *x == borrower => Some(i),
        _ => None,
    })
}

fn check(steps: &[Step]) -> Vec<Finding> {
    let mut findings: Vec<Finding> = Vec::new();

    // ---- 规则 1：E0382，move 之后再使用 ----
    let mut moved: Vec<(&'static str, usize)> = Vec::new();
    for (i, s) in steps.iter().enumerate() {
        match s {
            // 重新声明/赋值 = 重新获得所有权，之前那次 move 不再相关
            Step::Declare(v) => moved.retain(|(n, _)| n != v),
            Step::MoveOut(v) => moved.push((v, i)),
            Step::Use(v) => {
                if let Some((_, j)) = moved.iter().find(|(n, _)| n == v) {
                    push_unique(
                        &mut findings,
                        "E0382",
                        i,
                        format!("`{v}` 在第 {j} 步被 move 走后仍被使用（borrow of moved value）"),
                    );
                }
            }
            _ => {}
        }
    }

    // ---- 规则 2：E0499 / E0502，借用之间的冲突 ----
    let loans: Vec<Loan> = steps
        .iter()
        .enumerate()
        .filter_map(|(i, s)| match s {
            Step::Borrow(r, t, k) => Some(Loan {
                borrower: r,
                target: t,
                kind: *k,
                start: i,
            }),
            _ => None,
        })
        .collect();

    for (ai, a) in loans.iter().enumerate() {
        for b in loans.iter().skip(ai + 1) {
            if a.target != b.target {
                continue; // 不同 place，互不影响
            }
            let a_end = last_use(steps, a.borrower);
            // b 创建时 a 是否仍然存活？区间 [start, last_use]
            let a_alive = matches!(a_end, Some(e) if e >= b.start);
            if !a_alive {
                continue;
            }
            match (a.kind, b.kind) {
                (LoanKind::Mut, LoanKind::Mut) => push_unique(
                    &mut findings,
                    "E0499",
                    b.start,
                    format!(
                        "`{}` 在第 {} 步已是 {} 借用，此处又创建第二个可变借用（cannot borrow as mutable more than once at a time）",
                        a.target,
                        a.start,
                        a.kind.name()
                    ),
                ),
                (ka, kb) if ka != kb => push_unique(
                    &mut findings,
                    "E0502",
                    b.start,
                    format!(
                        "`{}` 的 {} 借用（第 {} 步，存活到第 {:?} 步）与 {} 借用共存（cannot borrow as mutable because it is also borrowed as immutable）",
                        a.target,
                        ka.name(),
                        a.start,
                        a_end,
                        kb.name()
                    ),
                ),
                _ => {} // 多个共享借用合法
            }
        }
    }

    // ---- 规则 3：E0596，通过共享引用改数据 ----
    for (i, s) in steps.iter().enumerate() {
        if let Step::Mutate(r) = s {
            if let Some(l) = loans.iter().find(|l| l.borrower == r) {
                if l.kind == LoanKind::Shared {
                    push_unique(
                        &mut findings,
                        "E0596",
                        i,
                        format!("`{r}` 是共享借用 `&{}`，其指向的数据不能可变借用", l.target),
                    );
                }
            }
        }
    }

    // ---- 规则 4：E0106，返回指向局部变量的引用 ----
    for (i, s) in steps.iter().enumerate() {
        if let Step::ReturnLocalRef(v) = s {
            push_unique(
                &mut findings,
                "E0106",
                i,
                format!("返回类型含借用值 `&{v}`，但没有可供借用的入参（missing lifetime specifier）"),
            );
        }
    }

    findings.sort_by_key(|f| (f.at, f.code));
    findings
}

// ---------------------------------------------------------------------------
// 夹具：用抽象语句复刻 The Book ch04-01 / ch04-02 里出现的代码
// ---------------------------------------------------------------------------
use Step::{Borrow, Declare, MoveOut, Mutate, ReturnLocalRef, Use};

fn fixtures() -> Vec<(&'static str, Vec<Step>, &'static [&'static str])> {
    vec![
        (
            "Listing 4-2: let s2 = s1; println!(\"{s1}\")",
            vec![Declare("s1"), MoveOut("s1"), Use("s1")],
            &["E0382"],
        ),
        (
            "两个同时存在的 &mut（Listing 4-7 相邻版本）",
            vec![
                Declare("s"),
                Borrow("r1", "s", LoanKind::Mut),
                Borrow("r2", "s", LoanKind::Mut),
                Use("r1"),
                Use("r2"),
            ],
            &["E0499"],
        ),
        (
            "共享借用存活期间创建可变借用（Listing 4-8）",
            vec![
                Declare("s"),
                Borrow("r1", "s", LoanKind::Shared),
                Borrow("r2", "s", LoanKind::Shared),
                Borrow("r3", "s", LoanKind::Mut),
                Use("r1"),
                Use("r2"),
                Use("r3"),
            ],
            &["E0502"],
        ),
        (
            "通过 & 改数据（Listing 4-6）",
            vec![
                Declare("s"),
                Borrow("r", "s", LoanKind::Shared),
                Mutate("r"),
            ],
            &["E0596"],
        ),
        (
            "fn dangle() -> &String 返回局部变量的引用",
            vec![Declare("s"), ReturnLocalRef("s")],
            &["E0106"],
        ),
        (
            "NLL 合法：共享借用的最后一次使用早于可变借用（Listing 4-10）",
            vec![
                Declare("s"),
                Borrow("r1", "s", LoanKind::Shared),
                Borrow("r2", "s", LoanKind::Shared),
                Use("r1"),
                Use("r2"),
                Borrow("r3", "s", LoanKind::Mut),
                Use("r3"),
            ],
            &[],
        ),
        (
            "NLL 合法：引用创建后从未使用 → 区间为空，不冲突",
            vec![
                Declare("s"),
                Borrow("r1", "s", LoanKind::Mut),
                Borrow("r2", "s", LoanKind::Mut),
                Use("r2"),
            ],
            &[],
        ),
    ]
}

/// 真实编译通过的 NLL 例子：证明“最后一个使用点在可变借用之前”即合法
fn nll_live_example() {
    let mut s = String::from("hello");
    let r1 = &s;
    let r2 = &s;
    println!("      r1={r1} r2={r2}    ← 共享引用最后一次使用");
    let r3 = &mut s; // 若 NLL 不生效，这里会报 E0502
    r3.push_str(", world");
    println!("      r3={r3}    ← 可变借用合法");
}

fn main() {
    println!("=== 借用检查器模型：复刻 rustc 的 5 类报错 ===\n");

    let mut ok = 0usize;
    let mut bad = 0usize;
    for (name, steps, expect) in fixtures() {
        let got = check(&steps);
        let codes: Vec<&str> = got.iter().map(|f| f.code).collect();
        let pass = codes == expect;
        if pass {
            ok += 1;
        } else {
            bad += 1;
        }
        println!("[{}] {name}", if pass { "PASS" } else { "FAIL" });
        println!("       语句数 = {}，期望错误码 = {:?}，实得 = {:?}", steps.len(), expect, codes);
        for f in &got {
            println!("         · {} @step {}：{}", f.code, f.at, f.what);
        }
    }

    println!("\n=== 真实编译通过的 NLL 用例（本二进制能跑起来就是证明）===");
    nll_live_example();

    println!("\n断言：{ok} 个夹具通过，{bad} 个失败");
    assert_eq!(bad, 0, "夹具期望与检查器实现不一致");
    println!("补充：5 个非法片段的**原文报错**见 compile_fail/README.md；");
    println!("      它们无法与本文件同 crate 编译，故以独立文件 + 检查器模型呈现。");
}
