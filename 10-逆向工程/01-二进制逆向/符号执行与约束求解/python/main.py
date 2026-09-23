"""653 · 符号执行与约束求解演示。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from symex_bv import BVV, evaluate  # noqa: E402
import symex_state as S  # noqa: E402


def banner(t):
    print("\n== %s ==" % t)


def main():
    banner("1. claripy 的位序：a[31] 是最左位")
    a = BVV(0x7FFFFFFF, 32)
    print("  0x7fffffff[31] = %d  (最左位)" % evaluate(a[31], {}))
    print("  0x7fffffff[0]  = %d  (最右位)" % evaluate(a[0], {}))
    w = BVV(0x01020304, 32)
    print("  chop(8)   =", [hex(evaluate(x, {})) for x in w.chop(8)])
    print("  get_byte  =", [hex(evaluate(w.get_byte(i), {})) for i in range(4)])

    banner("2. 整数被强制成左操作数的位宽")
    print("  BVV(1,8) + 300  -> 位宽 %d, 值 %d" % ((BVV(1, 8) + 300).size(),
                                                   evaluate(BVV(1, 8) + 300, {})))
    print("  BVV(0,8) - 1    -> %d（回绕）" % evaluate(BVV(0, 8) - 1, {}))
    print("  BVV(0,8) + True -> %d" % evaluate(BVV(0, 8) + True, {}))

    banner("3. 约束求解")
    st = S.SimState()
    x = st.solver.BVS("x", 8)
    st.solver.add(x > 3, x < 10)
    print("  约束 x>3, x<10 -> min=%d max=%d 全解=%s"
          % (st.solver.min(x), st.solver.max(x), st.solver.eval_upto(x, 20)))

    banner("4. 路径爆炸")
    sm = S.SimulationManager(active_states=[S.SimState(0)])
    for i in range(5):
        sm.step(step_func=lambda s: S.branch_states(s, 2))
        print("  第 %d 步：active=%d" % (i + 1, len(sm.active)))

    banner("5. explore 与 num_find 的累加")
    sm2 = S.SimulationManager(active_states=[S.SimState(0)],
                              step_func=S.linear_chain(100))
    sm2.explore(find=4)
    print("  find=4 -> found=%s" % [s.addr for s in sm2.found])
    sm3 = S.SimulationManager(active_states=[S.SimState(0)],
                              step_func=S.linear_chain(100))
    sm3._stashes["found"] = [S.SimState(99)]
    sm3.explore(find=5)
    print("  已有 1 个 found 后再 explore -> found=%s" % [s.addr for s in sm3.found])

    banner("6. stash 之间搬移")
    sm4 = S.SimulationManager(active_states=[S.SimState(i) for i in range(6)])
    sm4.stash(lambda s: s.addr < 3)
    sm4.drop(lambda s: s.addr == 4)
    print("  active=%s stashed=%s" % ([s.addr for s in sm4.active],
                                      [s.addr for s in sm4.stashed]))


if __name__ == "__main__":
    main()
