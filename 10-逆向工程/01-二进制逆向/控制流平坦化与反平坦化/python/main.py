"""652 · OLLVM 控制流平坦化与反平坦化演示。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import flatten as F  # noqa: E402

KEY = bytes(range(16))


def banner(t):
    print("\n== %s ==" % t)


def main():
    banner("1. case 值从哪来：scramble32")
    for i in range(5):
        print("  scramble32(%d, key) = 0x%08x" % (i, F.scramble32(i, KEY)))
    print("  换一个 key 全变：scramble32(0, 全零) = 0x%08x" % F.scramble32(0, bytes(16)))

    banner("2. 平坦化一个菱形 CFG")
    cfg = F.CFG("entry", {
        "entry": F.Block("entry", F.BR, ["B1", "B2"], cond="c0"),
        "B1": F.Block("B1", F.JMP, ["B3"]),
        "B2": F.Block("B2", F.JMP, ["B3"]),
        "B3": F.Block("B3", F.RET),
    })
    print("  原 CFG(c0=True) :", F.run_original(cfg, {"c0": True}))
    flat = F.flatten(cfg, KEY)
    for n in flat.order:
        print("    case 0x%08x -> %-12s %s" % (flat.case_of[n], n, flat.transition[n]))
    print("  switchVar 初值 = 0x%08x（= 首块 case）" % flat.initial)

    banner("3. 状态机与原 CFG 对拍")
    for cv in (True, False):
        seen, how = F.run_flattened(cfg, flat, {"c0": cv})
        print("  c0=%-5s -> %s (%s)" % (cv, seen, how))

    banner("4. 反平坦化：把 case 值还原成边")
    for n, e in F.deflatten(flat).items():
        print("  %-12s -> %s" % (n, e))

    banner("5. 回边兜底：fallback = scramble32(块数-1)")
    bcfg = F.CFG("e", {
        "e": F.Block("e", F.JMP, ["A"]),
        "A": F.Block("A", F.JMP, ["e"]),
        "B": F.Block("B", F.RET),
    })
    bf = F.flatten(bcfg, KEY)
    print("  order =", bf.order)
    print("  fallback = 0x%08x ；最后一块 case = 0x%08x"
          % (bf.fallback, bf.case_of[bf.order[-1]]))
    print("  A 的转移 =", bf.transition["A"])

    banner("6. 放弃条件")
    print("  含 invoke ->", F.flatten(F.CFG("e", {
        "e": F.Block("e", F.INVOKE, ["x"]), "x": F.Block("x", F.RET)}), KEY).reason)
    print("  单块     ->", F.flatten(F.CFG("e", {"e": F.Block("e", F.RET)}), KEY).reason)


if __name__ == "__main__":
    main()
