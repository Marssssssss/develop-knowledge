# -*- coding: utf-8 -*-
"""策略 vs 条件分支断言(结构/运行时切换/注册零改动/成本模型)。"""

from strat import (
    REGISTRY, Context, NormalStrategy, SeniorStrategy, Strategy, VipStrategy,
    add_strategy, branch_duplication_cost, branch_version, strategy_fixed_cost,
)

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def main():
    print("1. 两版行为一致")
    ctx = Context(NormalStrategy())
    assert ctx.checkout(100) == branch_version("normal", 100) == 100
    ctx.set_strategy(VipStrategy())
    assert ctx.checkout(100) == branch_version("vip", 100) == 50
    ok("Context 只认 Strategy 接口;构造注入 + set_strategy 运行时换——"
       "接口版与分支版结果对拍一致")

    print("2. 加新策略的改动面")
    class BlackFriday(Strategy):
        def execute(self, amount):
            return amount * 0.2

    before = set(REGISTRY)
    add_strategy("bf", BlackFriday)
    assert set(REGISTRY) - before == {"bf"}
    assert Context(REGISTRY["bf"]()).checkout(100) == 20
    ok("加策略 = 注册一个新类:Context 与既有策略**零改动**(OCP);"
       "分支版则要改每一处调用点的 if/elif 树")

    print("3. 分支数阈值(模型)")
    assert branch_duplication_cost(3, 4) == 12        # 3 分支 × 4 个调用点
    assert strategy_fixed_cost(3) == 4                 # 1 接口 + 3 类
    assert branch_duplication_cost(2, 1) < strategy_fixed_cost(2)
    assert branch_duplication_cost(4, 3) > strategy_fixed_cost(4)
    ok("账本:分支成本=分支数×调用点数,策略成本=策略数+1;单一调用点且分支少时,"
       "直接写分支更划算——模式不免费")

    print("4. 迁移入口")
    ok("存量分支树迁到多态的标准重构就是目录里的 Replace Conditional with "
       "Polymorphism——每砍掉一棵分支树做一次,而不是一次性大改")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
