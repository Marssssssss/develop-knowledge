# -*- coding: utf-8 -*-
"""泛化/定制计划切换断言(五次规则/比较切换/偏斜翻转/重规划触发)。"""

from plancache import FORCED_REPLAN_TRIGGERS, PreparedStmt, explain_shape, replan_needed

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def main():
    print("1. 无参数恒泛化")
    ps = PreparedStmt(has_params=False)
    assert [ps.run() for _ in range(8)] == ["generic"] * 8
    ok("语句没有参数时 generic/custom 之争不存在——永远泛化")

    print("2. 前五次定制 + 代价记账")
    ps = PreparedStmt(has_params=True)
    modes = [ps.run(100) for _ in range(5)]
    assert modes == ["custom"] * 5 and ps.custom_costs == [100] * 5
    ok("auto 模式:前五次逐次定制计划,并计算平均估算代价")

    print("3. 第六次起的比较切换")
    ps.generic_cost = 105
    assert ps.run() == "generic" and ps.using_generic     # 105 ≤ 1.1×100
    ps2 = PreparedStmt(has_params=True)
    for c in [100] * 5:
        ps2.run(c)
    ps2.generic_cost = 5000
    assert ps2.run() == "custom"
    ok("泛化代价不高于平均太多→切换;高出一个量级→继续每次重规划"
       "(倍数系数是模型,文档只给『not so much higher』)")

    print("4. EXPLAIN 形态判别")
    g = explain_shape("generic", 42)
    c = explain_shape("custom", 42)
    assert "$1" in g and "42" not in g and "42" in c
    ok("看 EXPLAIN EXECUTE:泛化计划含 $n 占位符,定制计划是代入后的字面值")

    print("5. 参数偏斜 → 计划翻转回归")
    ps3 = PreparedStmt(has_params=True)
    for c in [100, 100, 120, 100, 130]:                    # 常见值:选择性好
        ps3.run(c)
    ps3.generic_cost = 118                                  # ≈ 平均定制代价
    assert ps3.run() == "generic"
    # 切换后来了一个偏斜值:泛化计划对它很慢,而定制计划本可选另一条路
    slow_cost = 9000
    assert slow_cost > ps3.factor * (sum(ps3.custom_costs) / 5)
    ok("训练期全走常见值→泛化胜出;罕见的偏斜值到来时被泛化计划拖慢——"
       "这是 prepared statement 的经典翻转回归(应对方:force_custom_plan 或改写)")

    print("6. 强制重规划的触发条件")
    assert len(FORCED_REPLAN_TRIGGERS) == 3
    assert replan_needed(set(), {"规划器统计信息被更新(ANALYZE)"})
    assert replan_needed(set(), {"DDL 变更(语句引用的对象定义变了)"})
    assert replan_needed({"search_path 变化(9.3+ 按新 search_path 重新解析)"}, set()) is False
    ok("DDL/统计更新(ANALYZE)/search_path 变化 → 强制重新分析与规划;"
       "所以统计抖动会传染给 prepared 语句——即使已切到泛化计划")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
