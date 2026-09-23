"""653 符号执行与约束求解 —— 自检（实跑）。

A. 位向量语义：与 claripy bv.py 的文档示例与索引规则逐条对拍
B. 求解器：约束、eval/min/max、不可解
C. SimState 插件与 SimulationManager 的 stash 状态机
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from symex_bv import BVS, BVV, Extract, Concat, ZeroExt, SignExt, If, evaluate, solve  # noqa: E402
import symex_state as S  # noqa: E402

PASS = 0
FAIL = []


def check(c, m):
    global PASS
    if c:
        PASS += 1
    else:
        FAIL.append(m)


def eq(a, b, m):
    check(a == b, "%s: 期望 %r 实得 %r" % (m, b, a))


# ------------------------------------------------ A. 位序与切片

# 文档原文：a[31] 是最左位；0x7fffffff 写成二进制最左位是 0
a = BVV(0x7FFFFFFF, 32)
eq(evaluate(a[31], {}), 0, "0x7fffffff[31]")
eq(evaluate(a[0], {}), 1, "0x7fffffff[0]")
b = BVV(0xFFFFFFFE, 32)
eq(evaluate(b[0], {}), 0, "0xfffffffe[0]")
# a[31:30] 是两位最高位
eq(evaluate(BVV(0x3FFFFFFF, 32)[31:30], {}), 0, "0x3fffffff[31:30]")
eq(evaluate(BVV(0xFFFFFFFF, 32)[31:30], {}), 3, "0xffffffff[31:30]")
eq(evaluate(BVV(0xFFFFFFFC, 32)[1:0], {}), 0, "0xfffffffc[1:0]")
eq(BVV(0, 32)[31:30].size(), 2, "切片位宽 = hi-lo+1")
eq(BVV(0, 32)[7].size(), 1, "单位选择位宽 1")

# 负号下标按位宽折算
eq(evaluate(BVV(0x1, 32)[-1:0], {}), 1, "[-1:0] 等价于 [31:0]")

# chop：第一个元素是最左（最高）的那一段
w = BVV(0x01020304, 32)
eq([evaluate(x, {}) for x in w.chop(8)], [0x01, 0x02, 0x03, 0x04], "chop(8) 顺序")
eq([evaluate(x, {}) for x in w.chop(16)], [0x0102, 0x0304], "chop(16) 顺序")
eq(len(BVV(0, 32).chop(1)), 32, "chop(1) 得 32 段")
check(BVV(0, 32).chop(32) == [BVV(0, 32)] or len(BVV(0, 32).chop(32)) == 1,
      "chop(整宽) 返回自身")
try:
    BVV(0, 12).chop(5)
    check(False, "chop 不能整除应报错")
except ValueError:
    check(True, "chop 不能整除应报错")

# get_byte：大端字节序号，0 是最高字节
eq(evaluate(w.get_byte(0), {}), 0x01, "get_byte(0)")
eq(evaluate(w.get_byte(3), {}), 0x04, "get_byte(3)")
eq(evaluate(w.get_bytes(0, 2), {}), 0x0102, "get_bytes(0,2)")
try:
    w.get_byte(4)
    check(False, "get_byte 越界应报错")
except ValueError:
    check(True, "get_byte 越界应报错")

# 拼接与扩展：concat 时 self 在最左
eq(evaluate(BVV(1, 4).concat(BVV(2, 4)), {}), 0x12, "concat：self 在最高位")
eq(BVV(1, 4).concat(BVV(2, 4)).size(), 8, "concat 位宽相加")
# 文档示例
eq(evaluate(BVV(0b1111, 4).zero_extend(4), {}), 0b00001111, "zero_extend 示例")
eq(evaluate(BVV(0b1111, 4).sign_extend(4), {}), 0b11111111, "sign_extend 示例")
eq(evaluate(BVV(0b0111, 4).sign_extend(4), {}), 0b00000111, "正数 sign_extend 补 0")
eq(BVV(1, 4).zero_extend(4).size(), 8, "zero_extend 位宽")
eq(evaluate(ZeroExt(4, BVV(0xF, 4)), {}), 0x0F, "ZeroExt")
eq(evaluate(SignExt(4, BVV(0xF, 4)), {}), 0xFF, "SignExt")
eq(evaluate(Extract(15, 8, BVV(0x1234, 16)), {}), 0x12, "Extract 高字节")
eq(evaluate(Concat(BVV(0xAB, 8), BVV(0xCD, 8)), {}), 0xABCD, "Concat")

# _from_int：整数被强制成左操作数的位宽
e = BVV(1, 8) + 300
eq(e.size(), 8, "整数被强制成 8 位")
eq(evaluate(e, {}), (1 + 300) & 0xFF, "8 位回绕")
eq((BVV(3, 8) + 1).size(), 8, "小整数同样按 8 位")
eq(evaluate(BVV(3, 8) + 1, {}), 4, "3+1=4")
# _from_Bool：True -> BVV(1, like.length)
eq(evaluate(BVV(0, 8) + True, {}), 1, "True 变成同宽 BVV(1)")
eq(evaluate(BVV(0, 8) + False, {}), 0, "False 变成同宽 BVV(0)")
eq(evaluate(If(BVV(1, 1), BVV(7, 8), BVV(9, 8)), {}), 7, "If 真分支")
eq(evaluate(If(BVV(0, 1), BVV(7, 8), BVV(9, 8)), {}), 9, "If 假分支")

# 回绕：0-1 在 8 位下是 255
eq(evaluate(BVV(0, 8) - 1, {}), 255, "8 位下 0-1=255")


# ------------------------------------------------ B. 求解器

st = S.SimState()
x = st.solver.BVS("x", 8)
st.solver.add(x > 3)
st.solver.add(x < 10)
check(st.solver.satisfiable(), "应可解")
eq(st.solver.min(x), 4, "min(x) = 4")
eq(st.solver.max(x), 9, "max(x) = 9")
eq(st.solver.eval(x, 1), [4], "eval 默认取一个")
eq(st.solver.eval_upto(x, 20), list(range(4, 10)), "eval_upto 全解")

st2 = S.SimState()
y = st2.solver.BVS("y", 8)
st2.solver.add(y > 200)
st2.solver.add(y < 100)
check(not st2.solver.satisfiable(), "矛盾约束不可解")
eq(st2.solver.eval(y, 1), [], "不可解时 eval 为空")

# 求解器只管约束，不管状态：约束里的符号不在表达式里也能解
st3 = S.SimState()
p = st3.solver.BVS("p", 8)
q = st3.solver.BVS("q", 8)
st3.solver.add(p == 7)
st3.solver.add(q == p + 1)
eq(st3.solver.eval(q, 1), [8], "等式传播")
eq(st3.solver.eval(p, 1), [7], "p 被钉住")

# 无约束时按枚举顺序取第一个
st4 = S.SimState()
z = st4.solver.BVS("z", 8)
eq(st4.solver.eval(z, 1), [0], "无约束取枚举首个")
eq(len(st4.solver.eval_upto(z, 256)), 256, "无约束共 256 个解")


# ------------------------------------------------ C. 状态与管理器

st5 = S.SimState(addr=0x400000)
check("solver" in st5.plugins, "solver 是默认插件")
for name in ("regs", "mem", "memory", "inspect", "history", "scratch",
             "posix", "fs", "libc", "heap", "callstack"):
    check(name in st5.plugins, "插件 %s 存在" % name)
cp = st5.copy()
cp.addr = 0x500000
check(st5.addr != cp.addr, "copy 后互不影响")
cp.regs["rax"] = 1
check("rax" not in st5.regs, "regs 也是独立的")

eq(S.INTEGRAL_STASHES, ("active", "stashed", "pruned", "unsat", "errored",
                        "deadended", "unconstrained"), "integral stashes 顺序")
eq(S.ALL, "_ALL", "ALL 哨兵")
eq(S.DROP, "_DROP", "DROP 哨兵")

# step：无后继 -> deadended
sm = S.SimulationManager(active_states=[S.SimState(1)])
sm.step(step_func=lambda s: [])
eq(len(sm.active), 0, "active 清空")
eq(len(sm.deadended), 1, "进入 deadended")

# 二分派生 -> 路径数指数增长
sm2 = S.SimulationManager(active_states=[S.SimState(0)])
for _ in range(4):
    sm2.step(step_func=lambda s: S.branch_states(s, 2))
eq(len(sm2.active), 16, "4 层二分 -> 16 条路径")
eq(S.count_paths(4), 16, "路径数公式")

# unsat 状态：默认丢弃，save_unsat=True 时进 unsat
bad = S.SimState(0)
bad.solver.add(bad.solver.BVS("k", 8) > 5)
bad.solver.add(bad.solver.BVS("k", 8) < 3)
sm3 = S.SimulationManager(active_states=[bad])
sm3.step(step_func=lambda s: [s.copy()])
eq(len(sm3.active), 0, "不可解默认被丢掉")
eq(len(sm3.unsat), 0, "默认不保留 unsat")

bad2 = S.SimState(0)
bad2.solver.add(bad2.solver.BVS("k", 8) > 5)
bad2.solver.add(bad2.solver.BVS("k", 8) < 3)
sm4 = S.SimulationManager(active_states=[bad2], save_unsat=True)
sm4.step(step_func=lambda s: [s.copy()])
eq(len(sm4.unsat), 1, "save_unsat 时进 unsat stash")

# move / stash / drop / prune
sm5 = S.SimulationManager(active_states=[S.SimState(i) for i in range(6)])
moved = sm5.move("active", "stashed", lambda s: s.addr < 3)
eq(len(moved), 3, "move 按 filter 搬 3 个")
eq(len(sm5.active), 3, "active 剩 3 个")
eq(len(sm5.stashed), 3, "stashed 有 3 个")
sm5.stash(lambda s: s.addr == 3)
eq(len(sm5.stashed), 4, "stash 再搬 1 个")
sm5.drop(lambda s: s.addr == 4)
eq(len(sm5.active), 1, "drop 直接丢弃")
sm5.prune(lambda s: s.addr == 5)
eq(len(sm5.pruned), 1, "prune 进 pruned")

# explore：num_find 先把已有 found 计数加上
sm6 = S.SimulationManager(active_states=[S.SimState(0)],
                          step_func=S.linear_chain(100))
sm6.explore(find=4)
eq(len(sm6.found), 1, "explore 找到 1 个")
eq(sm6.found[0].addr, 4, "found 的地址")

# 已有 found 时 num_find 会累加：已有 1 个就要再找 1 个
sm7 = S.SimulationManager(active_states=[S.SimState(0)],
                          step_func=S.linear_chain(100))
sm7._stashes["found"] = [S.SimState(99)]
sm7.explore(find=5)
eq(len(sm7.found), 2, "已有 1 个 found 时会继续找到第 2 个")
eq(sm7.found[1].addr, 5, "第二个 found 的地址")

# avoid 的搬移先于 find
sm8 = S.SimulationManager(active_states=[S.SimState(0)],
                          step_func=S.linear_chain(100))
sm8.explore(find=9, avoid=3)
eq(len(sm8.avoid), 1, "avoid 命中 1 个")
eq(sm8.avoid[0].addr, 3, "avoid 的地址")
eq(len(sm8.active), 0, "走到 avoid 后停止")

# apply 产出到另一个 stash
sm9 = S.SimulationManager(active_states=[S.SimState(0)])
sm9.apply(state_func=lambda s: S.SimState(s.addr + 100), to_stash="stashed")
eq(sm9.stashed[0].addr, 100, "apply 写进 stashed")

print("断言通过: %d" % PASS)
if FAIL:
    print("失败 %d 条:" % len(FAIL))
    for m in FAIL:
        print("  -", m)
    sys.exit(1)
print("ALL GREEN")
