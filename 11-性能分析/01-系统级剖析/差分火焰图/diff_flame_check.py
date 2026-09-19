"""差分火焰图 —— 自检（实跑）。锚点来自 Differential Flame Graphs (2014-11-09)。"""

from diff_flame import (
    DiffFrame,
    apply_strip_hex,
    build_frames,
    cpi_frame,
    diff_folded,
    elided,
    format_three_column,
    max_abs_delta,
    mustacchi_width,
    normalize,
    normalize_scale,
    parse_three_column,
    self_delta,
    strip_hex,
    subtree_delta,
    total_width,
)

_passed = 0
_failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"ok   {label} {detail}")
    else:
        _failed += 1
        print(f"FAIL {label} {detail}")


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


# 原文给的三列样例
P1 = {"func_a;func_b;func_c": 31.0, "func_a": 4.0, "func_z": 10.0}
P2 = {"func_a;func_b;func_c": 33.0, "func_a": 9.0, "func_new": 7.0}

d = diff_folded(P1, P2)

# --- 1. difffolded.pl 的三列输出 ----------------------------------------------
lines = format_three_column(d)
check("每栈一行三列", all(len(l.rsplit(" ", 2)) == 3 for l in lines), f"{len(lines)} 行")
check("原文样例行原样复现",
      "func_a;func_b;func_c 31 33" in lines, "func_a;func_b;func_c 31 33")
check("三列可解析回来", parse_three_column(lines) == d, "")
check("只在一侧出现的栈补 0", d["func_new"] == (0.0, 7.0) and d["func_z"] == (10.0, 0.0),
      f"new={d['func_new']} z={d['func_z']}")

# --- 2. 宽度取 after，颜色取 delta --------------------------------------------
fr = {f.stack: f for f in build_frames(d)}
check("宽度取第二份 profile", fr["func_a;func_b;func_c"].width == 33.0,
      f"{fr['func_a;func_b;func_c'].width}")
check("delta = after − before", fr["func_a;func_b;func_c"].delta == 2.0, f"{fr['func_a;func_b;func_c'].delta}")
check("新增路径 delta 为整条", fr["func_new"].delta == 7.0, f"{fr['func_new'].delta}")
check("消失路径 delta 为负", fr["func_z"].delta == -10.0, f"{fr['func_z'].delta}")
check("总宽度 = 第二份样本总量", close(total_width(list(fr.values())), 49.0),
      f"{total_width(list(fr.values()))}")

md = max_abs_delta(list(fr.values()))
check("最大 |delta| 用于饱和度归一化", md == 10.0, f"{md}")
check("增长帧是红", fr["func_a;func_b;func_c"].hue(md).startswith("red"),
      fr["func_a;func_b;func_c"].hue(md))
check("减少帧是蓝", fr["func_z"].hue(md).startswith("blue"), fr["func_z"].hue(md))
check("无变化帧是白", DiffFrame("x", 5, 5).hue(md) == "white", DiffFrame("x", 5, 5).hue(md))
check("饱和度与 |delta| 成正比",
      fr["func_new"].hue(md) == "red@0.700" and fr["func_a;func_b;func_c"].hue(md) == "red@0.200",
      f"{fr['func_new'].hue(md)} / {fr['func_a;func_b;func_c'].hue(md)}")

# --- 3. 颜色只反映自身贡献，不含孩子 ------------------------------------------
d2 = diff_folded({"a": 10.0, "a;b": 10.0, "a;c": 10.0},
                 {"a": 15.0, "a;b": 30.0, "a;c": 30.0})
check("帧 a 自身 delta 只看 a 这一行", close(self_delta("a", d2), 5.0), f"{self_delta('a', d2)}")
check("子树 delta = 5 + 20 + 20 = 45", close(subtree_delta("a", d2), 45.0),
      f"{subtree_delta('a', d2)}")
check("自身 ≠ 子树（颜色不含孩子）", self_delta("a", d2) != subtree_delta("a", d2),
      f"{self_delta('a', d2)} vs {subtree_delta('a', d2)}")

# --- 4. -n 归一化 --------------------------------------------------------------
# 同一份代码、只是负载翻倍：不归一化会全红
L1 = {"a": 100.0, "b": 100.0}
L2 = {"a": 200.0, "b": 200.0}
raw = diff_folded(L1, L2)
nrm = diff_folded(normalize(L1, L2), L2)
check("不归一化时全部为正 delta", all(v2 > v1 for v1, v2 in raw.values()),
      f"{[(v1, v2) for v1, v2 in raw.values()]}")
check("归一化因子 = 总量之比", close(normalize_scale(L1, L2), 2.0), f"{normalize_scale(L1, L2)}")
check("归一化后 delta 全为 0（无回归）", all(close(v1, v2) for v1, v2 in nrm.values()),
      f"{[(v1, v2) for v1, v2 in nrm.values()]}")
check("归一化不改变第二份", all(close(v2, L2[k]) for k, (_, v2) in nrm.items()), "")

# --- 5. -x 剥十六进制地址 ------------------------------------------------------
H1 = {"app;foo+0x1a2b": 10.0}
H2 = {"app;foo+0x3c4d": 12.0}
hd = diff_folded(H1, H2)
check("地址不同 ⇒ 被当成两条栈", len(hd) == 2, f"{sorted(hd)}")
s1, s2 = apply_strip_hex(H1), apply_strip_hex(H2)
check("剥地址后合并成一条", set(s1) == set(s2) and len(set(s1)) == 1, f"{set(s1)}")
check("剥地址后只剩真实差异", close(s2["app;foo+"] - s1["app;foo+"], 2.0),
      f"{s2['app;foo+'] - s1['app;foo+']}")
check("strip_hex 只去地址不动函数名", strip_hex("app;foo+0x1a2b") == "app;foo+", strip_hex("app;foo+0x1a2b"))

# --- 6. 消失路径与 --negate ----------------------------------------------------
el = elided(P1, P2)
check("消失栈 1 条", el["count"] == 1.0, f"{el['count']}")
check("消失占比 22.2%", close(el["pct"], 10.0 * 100.0 / 45.0, 1e-9), f"{el['pct']:.4f}%")
check("完全覆盖时 elided=0", elided({"a": 1.0}, {"a": 1.0})["pct"] == 0.0, "")

neg = {f.stack: f for f in build_frames(d, negate=True)}
check("--negate 后宽度取第一份", neg["func_a;func_b;func_c"].width == 31.0,
      f"{neg['func_a;func_b;func_c'].width}")
check("--negate 反转颜色", neg["func_z"].delta == 10.0 and neg["func_z"].hue(md).startswith("red"),
      f"{neg['func_z'].delta} {neg['func_z'].hue(md)}")
check("--negate 后总宽度 = 第一份总量", close(total_width(list(neg.values())), 45.0),
      f"{total_width(list(neg.values()))}")

# --- 7. 其它差分形态 ----------------------------------------------------------
f = DiffFrame("x", 10, 25)
check("Mustacchi 方案：宽度就是 delta", mustacchi_width(f) == 15.0, f"{mustacchi_width(f)}")
check("本站方案：宽度是 after", f.width == 25.0, f"{f.width}")
cf = cpi_frame("a;b", cycles=1000, stall_cycles=800)
check("CPI 帧的 delta 是 cycles − stall", cf.delta == 200.0, f"{cf.delta}")
check("CPI 帧宽度取 cycles", cf.width == 1000.0, f"{cf.width}")

print(f"\n{_passed} passed, {_failed} failed")
raise SystemExit(1 if _failed else 0)
