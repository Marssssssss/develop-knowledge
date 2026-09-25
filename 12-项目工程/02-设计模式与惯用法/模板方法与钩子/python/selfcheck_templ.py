# -*- coding: utf-8 -*-
"""模板方法与钩子断言(结构锁定/三类步骤/骨架继承 vs 组合)。"""

from templ import STEP_KINDS, GameAI, MonstersAI, OrcsAI

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def main():
    print("1. 骨架不可覆写,步骤可")
    orc = OrcsAI()
    steps = orc.template_turn()
    assert steps == ["collect:shared", "build:farm", "build:barracks",
                     "build:peon", "build:grunt", "attack:default"]
    ok("模板方法定死调用序:collect→build→attack;子类只填/覆写步骤,"
       "**不覆写模板方法本身**(结构变更只发生在基类一处)")

    print("2. 未实现抽象步骤")
    class Lazy(GameAI):
        pass
    try:
        Lazy().template_turn()
        raise AssertionError("unreachable")
    except NotImplementedError:
        ok("抽象步骤不实现,骨架一跑就失败——基类强制子类交代每个必需步骤")

    print("3. 三类步骤")
    assert set(STEP_KINDS) == {"abstract", "default", "hook"}
    monster = MonstersAI()
    ms = monster.template_turn()
    assert ms[0] == "hook:monsters-awake" and "collect:shared" not in ms
    ok("钩子=空体可选(不复写也能跑,放在关键步骤前后);默认步骤被压制成空——"
       "官方 Cons:子类压制默认实现**可能违反 LSP**( MonstersAI 已不是『会采集的 AI』)")

    print("4. 与策略的分工(官方 Relations)")
    ok("Template Method 基于**继承**:类级、静态,编译期定型;Strategy 基于**组合**:"
       "对象级、运行时可换;Factory Method 是 Template Method 的特化——"
       "『创建对象』这个步骤被做成钩子族")

    print("5. 维护性警示")
    ok("步骤越多模板越难维护(官方 Cons):骨架是好东西,但不是越多越好;"
       "当多个子类开始互相配合地覆写同一组步骤时,说明该拆的是算法本身")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
