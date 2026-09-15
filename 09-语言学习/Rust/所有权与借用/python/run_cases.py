#!/usr/bin/env python3
"""所有权与借用检查的夹具与主程序（Rust demo 1/5 · Python 侧）

夹具全部复刻 The Rust Programming Language ch04-01 / ch04-02 的代码片段；
检查器本体在 borrow_checker.py。

运行：python3 run_cases.py
"""

from borrow_checker import (
    MUT,
    SHARED,
    Borrow,
    BorrowChecker,
    Declare,
    MoveOut,
    Mutate,
    ReturnLocalRef,
    Use,
)
from typing import List, Tuple

# --------------------------------------------------------------------------
# 夹具：复刻 The Book ch04-01 / ch04-02 的代码片段
# --------------------------------------------------------------------------
CASES: List[Tuple[str, List[object], Tuple[str, ...]]] = [
    (
        'Listing 4-2: let s2 = s1; println!("{s1}")',
        [Declare("s1"), MoveOut("s1"), Use("s1")],
        ("E0382",),
    ),
    (
        "两个同时存在的 &mut",
        [
            Declare("s"),
            Borrow("r1", "s", MUT),
            Borrow("r2", "s", MUT),
            Use("r1"),
            Use("r2"),
        ],
        ("E0499",),
    ),
    (
        "共享借用存活期间创建可变借用（Listing 4-8）",
        [
            Declare("s"),
            Borrow("r1", "s", SHARED),
            Borrow("r2", "s", SHARED),
            Borrow("r3", "s", MUT),
            Use("r1"),
            Use("r2"),
            Use("r3"),
        ],
        ("E0502",),
    ),
    (
        "通过 & 改数据（Listing 4-6）",
        [Declare("s"), Borrow("r", "s", SHARED), Mutate("r")],
        ("E0596",),
    ),
    (
        "fn dangle() -> &String",
        [Declare("s"), ReturnLocalRef("s")],
        ("E0106",),
    ),
    (
        "NLL 合法：共享借用最后一次使用早于可变借用（Listing 4-10）",
        [
            Declare("s"),
            Borrow("r1", "s", SHARED),
            Borrow("r2", "s", SHARED),
            Use("r1"),
            Use("r2"),
            Borrow("r3", "s", MUT),
            Use("r3"),
        ],
        (),
    ),
    (
        "NLL 合法：引用创建后从未使用 → 空区间",
        [
            Declare("s"),
            Borrow("r1", "s", MUT),
            Borrow("r2", "s", MUT),
            Use("r2"),
        ],
        (),
    ),
]

NLL_CASE_INDEX = 5  # 用它在两种模型下对比


def main() -> None:
    print("=== 借用检查器模型：复刻 rustc 的 5 类报错 ===\n")
    ok = bad = 0
    for name, steps, expect in CASES:
        got = tuple(x.code for x in BorrowChecker(steps).check())
        passed = got == expect
        ok, bad = (ok + 1, bad) if passed else (ok, bad + 1)
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        print(f"       语句数={len(steps)}  期望={expect}  实得={got}")
        for x in BorrowChecker(steps).check():
            print(f"         · {x.code} @step {x.at}：{x.what}")

    print("\n=== 词法作用域模型 vs NLL 模型（同一个片段，两种判定）===")
    name, steps, _ = CASES[NLL_CASE_INDEX]
    print(f"  片段：{name}")
    for label, lexical in (("词法作用域（NLL 之前）", True), ("NLL（Rust 2018+）", False)):
        ck = BorrowChecker(steps, lexical=lexical)
        codes = tuple(x.code for x in ck.check())
        print(f"\n  [{label}] 判定 = {codes or '合法'}")
        print(ck.timeline(label))
    lexical_codes = tuple(x.code for x in BorrowChecker(steps, lexical=True).check())
    nll_codes = tuple(x.code for x in BorrowChecker(steps).check())
    assert lexical_codes == ("E0502",), lexical_codes
    assert nll_codes == (), nll_codes

    print("\n=== 统计 ===")
    all_loans = sum(len(BorrowChecker(s).loans()) for _, s, _ in CASES)
    codes_seen = sorted({c for _, s, _ in CASES for c in (x.code for x in BorrowChecker(s).check())})
    print(f"  夹具 {len(CASES)} 个（{ok} 通过 / {bad} 失败），抽象语句 {sum(len(s) for _, s, _ in CASES)} 条，借用 {all_loans} 次")
    print(f"  覆盖错误码：{codes_seen}")
    print(f"  NLL 收益：词法模型下 E0502 → NLL 下合法（该片段释放了 {len(steps)} 条语句中的 1 条误报）")

    assert bad == 0, "夹具期望与检查器实现不一致"
    print("\n断言全部通过。")


if __name__ == "__main__":
    main()
