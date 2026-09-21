"""Go 方法集与选择器模型的自检：断言来自 go.dev/ref/spec 的规范原文。"""

import sys

from selectormodel import (
    Universe, GoType, Ambiguous, NotFound,
    method_set, lookup, selector,
)

PASS = [0]
FAIL = [0]


def ok(cond, label):
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
        print("FAIL: " + label)


def eq(got, want, label):
    ok(got == want, "%s (got=%r want=%r)" % (label, got, want))


def raises(exc, fn, label):
    try:
        fn()
    except exc:
        PASS[0] += 1
        return
    except Exception as e:  # noqa
        FAIL[0] += 1
        print("FAIL: %s (raised %r)" % (label, e))
        return
    FAIL[0] += 1
    print("FAIL: %s (no exception)" % label)


def official_universe():
    """规范 §Selectors 官方示例里的 T0/T1/T2/Q。"""
    u = Universe()
    u.add(GoType("T0", fields={"x": "int"}, methods={"M0": True}))    # func (*T0) M0()
    u.add(GoType("T1", fields={"y": "int"}, methods={"M1": False}))   # func (T1) M1()
    u.add(GoType("T2", fields={"z": "int"},
                 embedded=[("T1", "T1", False), ("T0", "T0", True)],
                 methods={"M2": True}))                                # func (*T2) M2()
    u.add(GoType("Q", is_defined_pointer_of="T2"))
    return u


# ---------------------------------------------------------------- E1 T 与 *T 的方法集
def e1_method_set_basics():
    u = official_universe()
    eq(set(method_set(u, "T0", False)), set(), "E1 T0 的方法集为空（M0 是指针接收者）")
    eq(set(method_set(u, "T0", True)), {"M0"}, "E1 *T0 的方法集是 {M0}")
    eq(set(method_set(u, "T1", False)), {"M1"}, "E1 T1 的方法集是 {M1}")
    eq(set(method_set(u, "T1", True)), {"M1"}, "E1 *T1 也含 M1（值接收者被 *T 包含）")
    ok(set(method_set(u, "T0", False)) <= set(method_set(u, "T0", True)),
       "E1 T 的方法集是 *T 的子集")


# ---------------------------------------------------------------- E2 嵌入带来的提升
def e2_promotion():
    u = official_universe()
    s2 = method_set(u, "T2", False)
    p2 = method_set(u, "T2", True)
    # 嵌入 T1（值）：S 与 *S 都获得接收者为 T1 的方法
    ok("M1" in s2, "E2 T2 的方法集含 M1（经嵌入 T1 提升）")
    ok("M1" in p2, "E2 *T2 的方法集含 M1")
    # 嵌入 *T0：S 与 *S 都获得接收者为 T0 或 *T0 的方法
    ok("M0" in s2, "E2 T2 的方法集含 M0（经嵌入 *T0 提升）")
    ok("M0" in p2, "E2 *T2 的方法集含 M0")
    # M2 是指针接收者，只属于 *T2
    ok("M2" not in s2, "E2 M2 不在 T2 的方法集里")
    ok("M2" in p2, "E2 M2 在 *T2 的方法集里")
    eq(set(s2), {"M0", "M1"}, "E2 T2 的方法集 = {M0, M1}")
    eq(set(p2), {"M0", "M1", "M2"}, "E2 *T2 的方法集 = {M0, M1, M2}")
    # 深度：M1 与 M0 的深度都是 1（提升一层），M2 是自己声明的 0
    eq(s2["M1"][2], 1, "E2 M1 深度 1")
    eq(s2["M0"][2], 1, "E2 M0 深度 1")
    eq(p2["M2"][2], 0, "E2 M2 深度 0")


# ---------------------------------------------------------------- E3 嵌入值 vs 嵌入指针
def e3_embed_value_vs_pointer():
    # 嵌入值类型 T：只有 *S 才拿到 *T 接收者的方法
    u = Universe()
    u.add(GoType("A", methods={"Val": False, "Ptr": True}))
    u.add(GoType("S1", embedded=[("A", "A", False)]))
    u.add(GoType("S2", embedded=[("A", "A", True)]))
    eq(set(method_set(u, "S1", False)), {"Val"}, "E3 嵌入 A：S 只拿到值接收者方法")
    eq(set(method_set(u, "S1", True)), {"Val", "Ptr"}, "E3 嵌入 A：*S 两种都拿")
    eq(set(method_set(u, "S2", False)), {"Val", "Ptr"}, "E3 嵌入 *A：S 两种都拿")
    eq(set(method_set(u, "S2", True)), {"Val", "Ptr"}, "E3 嵌入 *A：*S 两种都拿")


# ---------------------------------------------------------------- E4 官方示例的选择器
def e4_official_selectors():
    u = official_universe()
    # t.z 深度 0，t.y / t.x 深度 1
    eq(lookup(u, "T2", "z"), (0, [], "field", "T2"), "E4 t.z 深度 0")
    eq(lookup(u, "T2", "y"), (1, ["T1"], "field", "T1"), "E4 t.y = t.T1.y 深度 1")
    eq(lookup(u, "T2", "x"), (1, ["T0"], "field", "T0"), "E4 t.x = (*t.T0).x 深度 1")
    # p 是 *T2，规则相同
    eq(lookup(u, "T2", "z"), lookup(u, "T2", "z"), "E4 p.z 与 t.z 同解")
    # 方法
    eq(lookup(u, "T2", "M0")[:3], (1, ["T0"], "method"), "E4 p.M0() 经 *T0 找到")
    eq(lookup(u, "T2", "M1")[:3], (1, ["T1"], "method"), "E4 p.M1() 经 T1 找到")
    eq(lookup(u, "T2", "M2")[:3], (0, [], "method"), "E4 p.M2() 是 T2 自己的")
    # 嵌入字段本身也可选
    eq(lookup(u, "T2", "T1")[2], "field", "E4 t.T1 选到嵌入字段本身")
    eq(lookup(u, "T2", "T0")[2], "field", "E4 t.T0 选到嵌入字段本身")


# ---------------------------------------------------------------- E5 定义型指针的例外
def e5_defined_pointer():
    u = official_universe()
    res = selector(u, "Q", "x")
    eq(res[2], "field", "E5 q.x 合法（(*q).x 是字段选择器）")
    raises(NotFound, lambda: selector(u, "Q", "M0"),
           "E5 q.M0() 非法：(*q).M0 是方法不是字段，不适用该例外")
    raises(NotFound, lambda: selector(u, "Q", "M1"), "E5 q.M1() 同样非法")
    eq(selector(u, "Q", "z")[2], "field", "E5 q.z 合法")


# ---------------------------------------------------------------- E6 最浅深度与歧义
def e6_depth_and_ambiguity():
    u = Universe()
    u.add(GoType("A", fields={"F": "int"}, methods={"M": False}))
    u.add(GoType("B", fields={"F": "int"}, methods={"M": False}))
    u.add(GoType("C", embedded=[("A", "A", False), ("B", "B", False)]))
    raises(Ambiguous, lambda: lookup(u, "C", "F"), "E6 两处同深度的 F 是歧义")
    raises(Ambiguous, lambda: lookup(u, "C", "M"), "E6 两处同深度的 M 是歧义")
    # 自己声明的（深度 0）会遮蔽提升的（深度 1）
    u.add(GoType("D", fields={"F": "string"},
                 embedded=[("A", "A", False), ("B", "B", False)]))
    eq(lookup(u, "D", "F"), (0, [], "field", "D"), "E6 深度 0 的字段遮蔽深度 1 的提升")
    # 深度不同则浅者胜
    u.add(GoType("E1", fields={"F": "int"}))
    u.add(GoType("E2", embedded=[("E1", "E1", False)]))
    u.add(GoType("E3", embedded=[("E2", "E2", False)], fields={"F": "bool"}))
    eq(lookup(u, "E3", "F")[0], 0, "E6 自身声明优先")
    u2 = Universe()
    u2.add(GoType("P1", fields={"F": "int"}))
    u2.add(GoType("P2", embedded=[("P1", "P1", False)]))
    eq(lookup(u2, "P2", "F"), (1, ["P1"], "field", "P1"), "E6 逐层提升深度 +1")


# ---------------------------------------------------------------- E7 深度与 BFS 的一致性
def e7_depth_matches_bfs():
    u = Universe()
    u.add(GoType("L0", fields={"Tgt": "int"}))
    u.add(GoType("L1", embedded=[("L0", "L0", False)]))
    u.add(GoType("L2", embedded=[("L1", "L1", False)]))
    u.add(GoType("L3", embedded=[("L2", "L2", False)]))
    eq(lookup(u, "L3", "Tgt")[0], 3, "E7 三层嵌入深度 3")
    eq(lookup(u, "L3", "Tgt")[1], ["L2", "L1", "L0"], "E7 路径逐层展开")
    # 与「枚举所有路径取最小」对拍
    def all_depths(tname, depth=0, seen=()):
        t = u.types[tname]
        if "Tgt" in t.fields:
            yield depth
        for _fn, sub, _p in t.embedded:
            if sub in seen:
                continue
            yield from all_depths(sub, depth + 1, seen + (tname,))
    eq(lookup(u, "L3", "Tgt")[0], min(all_depths("L3")), "E7 与穷举最小深度一致")


# ---------------------------------------------------------------- E8 接口方法集是交集
def e8_interface_intersection():
    u = Universe()
    u.add(GoType("IA", methods={"M": False, "N": False}))
    u.add(GoType("IB", methods={"N": False, "O": False}))
    u.add(GoType("I", is_interface=True, type_set=["IA", "IB"]))
    eq(set(method_set(u, "I", False)), {"N"}, "E8 接口方法集 = 类型集方法集的交集")
    raises(NotFound, lambda: lookup(u, "I", "M"), "E8 不在方法集里的 M 非法")
    eq(lookup(u, "I", "N")[2], "method", "E8 N 在接口方法集里")


# ---------------------------------------------------------------- E9 顶层类型的边界
def e9_edges():
    u = official_universe()
    raises(NotFound, lambda: lookup(u, "T2", "Nope"), "E9 不存在的标识符")
    raises(NotFound, lambda: lookup(u, "T0", "y"), "E9 跨类型取不到")
    # 空白标识符不能做选择器（规范：it must not be the blank identifier）
    u2 = Universe()
    u2.add(GoType("Z", fields={"_": "int"}))
    # 本模型不在方法集里收录 _，故选择器取不到
    raises(NotFound, lambda: lookup(u2, "Z", "_"), "E9 空白标识符不是合法选择器")


def main():
    for fn in (e1_method_set_basics, e2_promotion, e3_embed_value_vs_pointer,
               e4_official_selectors, e5_defined_pointer, e6_depth_and_ambiguity,
               e7_depth_matches_bfs, e8_interface_intersection, e9_edges):
        fn()
    print("PASS=%d FAIL=%d" % (PASS[0], FAIL[0]))
    return 1 if FAIL[0] else 0


if __name__ == "__main__":
    sys.exit(main())
