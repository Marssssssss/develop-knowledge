#!/usr/bin/env python3
r"""Auto Layout 背后的 Cassowary 型约束求解 —— 可执行参考模型 + 自检。

权威依据(实际读过):
  * Apple《Auto Layout Guide》*Anatomy of a Constraint* —— 约束的线性方程形式
    `item1.attribute1 = multiplier × item2.attribute2 + constant`、三种关系
    (= / >= / <=)、优先级取值 1..1000(1000 = required)、CHCR 默认
    Hugging = 250 / CompressionResistance = 750、方程反转规则(乘数取倒数、
    常量取反)、"未满足的可选约束像一股力把视图拉向它"。
  * Badros / Borning / Stuckey《The Cassowary Linear Arithmetic Constraint
    Solving Algorithm》(TOCHI 8(4) 2001) —— 约束层级(constraint hierarchy)、
    required 与非 required 强度、slack 变量把不等式化成等式、error 变量承载
    "差多少"、目标是误差的加权和最小、edit / stay 约束与增量重解。

实现取舍(与 README「与 Cassowary 的差异」一节对应):
  * 保留:误差变量 + 强度加权目标 + 单纯形 + edit/stay 约束;
  * 简化:每次 solve 重建表,不做对偶单纯形增量维护 —— 增量只体现为
    "只改一个常数再解一次",并在自检里**实测**它与冷启动的主元数差异;
  * 自由变量拆成 x = x⁺ - x⁻(两个非负列),故列数约为变量数的 2 倍。

运行: python3 cassowary_check.py
"""

from __future__ import annotations

TOL = 1e-9

from solver_core import (  # noqa: E402 —— 必须在模块文档字符串之后
    MEDIUM,
    REQUIRED,
    STRONG,
    WEAK,
    Constraint,
    E,
    Infeasible,
    Solver,
    Var,
    apple,
    invert,
    residual,
    strength,
)

# ---------------------------------------------------------------------------
# 5. 自检
# ---------------------------------------------------------------------------
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(label)
    print(("  [PASS] " if ok else "  [FAIL] ") + label + (f"   {detail}" if detail else ""))


def near(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def t_strength_table() -> None:
    print("[1] 强度刻度与 Apple 优先级映射")
    check("required > strong > medium > weak > 0",
          REQUIRED > STRONG > MEDIUM > WEAK > 0, f"{REQUIRED} / {STRONG:g} / {MEDIUM:g} / {WEAK:g}")
    check("apple(250) < apple(500) < apple(750) < apple(1000)=required",
          apple(250) < apple(500) < apple(750) < apple(1000) == REQUIRED)
    check("CHCR 默认 Hugging(250) < CompressionResistance(750)", apple(250) < apple(750))


def t_inversion() -> None:
    print("[2] 方程反转规则(乘数取倒数 / 常量取反)")
    w, h = Var("width"), Var("height")
    fwd = E((h, 1.0), (w, -2.0))                        # height = 2.0 × width + 0.0
    rev = invert(fwd, pivot=w)
    check("height = 2·width  ↔  width = 0.5·height(乘数 2.0→0.5)",
          near(rev[w], 1.0) and near(rev[h], -0.5) and near(rev[None], 0.0), f"{rev}")
    a, b = Var("a.trailing"), Var("b.leading")
    c1 = E((b, 1.0), (a, -1.0), const=-8.0)             # b = 1.0 × a + 8.0
    c2 = invert(c1, pivot=a)
    check("b = 1.0·a + 8.0  ↔  a = 1.0·b - 8.0(常量取反)",
          near(c2[a], 1.0) and near(c2[b], -1.0) and near(c2[None], 8.0),
          f"{c2} → a - b + 8 = 0 ⇒ a = b - 8")


def t_row_layout() -> None:
    """可伸缩行:窗宽 300 required,两个盒子的宽度是 weak 偏好。"""
    print("[3] 可伸缩行(required 定骨架 + weak 定偏好)")
    s = Solver()
    w = s.var("windowWidth")
    l1, r1 = s.var("box1.left"), s.var("box1.right")
    l2, r2 = s.var("box2.left"), s.var("box2.right")
    s.add(E((w, 1.0), const=-300.0), "==", REQUIRED, "windowWidth=300")
    s.add(E((l1, 1.0)), "==", REQUIRED, "box1.left=0")
    s.add(E((r2, 1.0), (w, -1.0)), "==", REQUIRED, "box2.right=windowWidth")
    s.add(E((l2, 1.0), (r1, -1.0)), ">=", REQUIRED, "box2.left>=box1.right")
    s.add(E((r1, 1.0), (l1, -1.0)), ">=", REQUIRED, "width1>=0")
    s.add(E((r2, 1.0), (l2, -1.0)), ">=", REQUIRED, "width2>=0")
    c7 = s.add(E((r1, 1.0), (l1, -1.0), const=-50.0), "==", WEAK, "prefer width1=50")
    c8 = s.add(E((r2, 1.0), (l2, -1.0), const=-100.0), "==", WEAK, "prefer width2=100")
    v = s.solve()
    check("box1.right = 50", near(v[r1], 50.0), f"{v[r1]:g}")
    check("box2.left = 200", near(v[l2], 200.0), f"{v[l2]:g}")
    check("box2.right = 300", near(v[r2], 300.0), f"{v[r2]:g}")
    check("总宽守恒 50 + 150 + 100 = 300",
          near(v[r1] + (v[l2] - v[r1]) + (v[r2] - v[l2]), 300.0))
    check("两条 weak 偏好都被完全满足(偏差 0)",
          near(residual(c7, v), 0.0) and near(residual(c8, v), 0.0))


def t_priority_conflict() -> None:
    """required 100 vs weak 50;strong 10 vs weak 20。"""
    print("[4] 优先级冲突:required > strong > weak")
    s = Solver()
    x = s.var("x")
    s.add(E((x, 1.0), const=-100.0), "==", REQUIRED, "required width=100")
    weak = s.add(E((x, 1.0), const=-50.0), "==", WEAK, "weak width=50")
    v = s.solve()
    check("required 100 压过 weak 50 → x = 100", near(v[x], 100.0), f"{v[x]:g}")
    check("weak 被牺牲,偏差 = 50", near(residual(weak, v), 50.0), f"{residual(weak, v):g}")

    s2 = Solver()
    z = s2.var("z")
    s2.add(E((z, 1.0), const=-10.0), "==", STRONG, "strong z=10")
    s2.add(E((z, 1.0), const=-20.0), "==", WEAK, "weak z=20")
    v2 = s2.solve()
    check("strong 10 压过 weak 20 → z = 10", near(v2[z], 10.0), f"{v2[z]:g}")


def t_chcr() -> None:
    """CHCR:容器 300,label 内在 80,textField 内在 100。

    两段对照正是 Apple 文档 *CHCR 处理指南* 第 1 条:一排视图 hugging 优先级**相同**
    时布局有歧义("不知该拉伸哪个");把 textField 的 hugging 调得比 label 低
    (Interface Builder 会自动把所有 label 设成 251)才能唯一确定。
    """
    print("[5] CHCR:先复现歧义,再靠 hugging 优先级差消歧")

    def build(label_hug: int, field_hug: int) -> Solver:
        s = Solver()
        a, b = s.var("label.width"), s.var("field.width")
        s.add(E((a, 1.0), (b, 1.0), const=-300.0), "==", REQUIRED, "a+b=300")
        s.add(E((a, 1.0), const=-80.0), ">=", apple(750), "label CR 750: >=80")
        s.add(E((a, 1.0), const=-80.0), "<=", apple(label_hug), "label Hug")
        s.add(E((b, 1.0), const=-100.0), ">=", apple(750), "field CR 750: >=100")
        s.add(E((b, 1.0), const=-100.0), "<=", apple(field_hug), "field Hug")
        return s

    def cost(s: Solver, values: dict[Var, float]) -> float:
        return sum(c.strength * residual(c, values) for c in s.cons if c.strength < REQUIRED)

    # --- 场景 A:hugging 相同 → 解在 [80,220] 区间内平坦,是"合法歧义" ---
    sa = build(250, 250)
    va = sa.solve()
    a, b = sa.vars[0], sa.vars[1]
    check("等 hugging 时解落在 80..220 内(CR 保证不越界)",
          80.0 - 1e-6 <= va[a] <= 220.0 + 1e-6 and near(va[a] + va[b], 300.0),
          f"label={va[a]:g} field={va[b]:g}")
    extreme_a = {**va, a: 80.0, b: 220.0}
    extreme_b = {**va, a: 200.0, b: 100.0}
    ca, cb = cost(sa, extreme_a), cost(sa, extreme_b)
    check("两个端点解代价相同 → 目标函数在可行域上平坦,布局歧义",
          near(ca, cb, 1.0), f"cost(80,220)={ca:.3g} cost(200,100)={cb:.3g}")

    # --- 场景 B:label hugging 251 > field 250 → 唯一解 label 不拉伸 ---
    sb = build(251, 250)
    vb = sb.solve()
    a2, b2 = sb.vars[0], sb.vars[1]
    check("label Hug 251 后:label 保持内在宽 80", near(vb[a2], 80.0), f"{vb[a2]:g}")
    check("剩余空间归 textField → 220", near(vb[b2], 220.0), f"{vb[b2]:g}")
    check("label 两条约束都没被违反(CR 与 Hug 同时满足)",
          near(residual(sb.cons[1], vb), 0.0) and near(residual(sb.cons[2], vb), 0.0))
    check("field 的 Hugging 被违反 120 点(它阻力更弱,所以让步)",
          near(residual(sb.cons[4], vb), 120.0), f"{residual(sb.cons[4], vb):g}")


def t_stay_and_edit() -> None:
    """编辑约束(拖动)+ stay(weak):验证"只有被拖的点动"。"""
    print("[6] 编辑约束 + stay 约束(最小位移)")
    s = Solver()
    m = [s.var(f"m{i}") for i in range(4)]
    for i, mv in enumerate(m):
        s.stay(mv, 100.0 * (i + 1))                    # 原本停在 100/200/300/400
    drag = s.add(E((m[0], 1.0), const=-100.0), "==", STRONG, "drag m0")
    v0 = s.solve()
    cold = s.pivots
    check("未拖动时 stay 全部满足",
          all(near(v0[m[i]], 100.0 * (i + 1)) for i in range(4)))
    drag.lhs = E((m[0], 1.0), const=-250.0)            # 拖到 250
    v1 = s.solve()
    check("拖动后 m0 = 250(strong 压过 weak stay)", near(v1[m[0]], 250.0), f"{v1[m[0]]:g}")
    check("其余点纹丝不动(stay 未被牵连)",
          near(v1[m[1]], 200.0) and near(v1[m[2]], 300.0) and near(v1[m[3]], 400.0))
    print(f"         冷启动主元数 = {cold};改一个常数后重解的主元数 = {s.pivots}")


def t_infeasible() -> None:
    print("[7] required 冲突检测")
    s = Solver()
    x = s.var("x")
    s.add(E((x, 1.0), const=-10.0), "==", REQUIRED, "x=10")
    s.add(E((x, 1.0), const=-20.0), "==", REQUIRED, "x=20")
    try:
        s.solve()
        check("两条矛盾的 required 应抛 Infeasible", False)
    except Infeasible:
        check("两条矛盾的 required 抛 Infeasible(对应 Auto Layout 打印冲突日志)",
              True, "x==10 与 x==20 不能同时 required")


if __name__ == "__main__":
    for fn in (t_strength_table, t_inversion, t_row_layout, t_priority_conflict,
               t_chcr, t_stay_and_edit, t_infeasible):
        fn()
    print(f"\n断言 {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项:", FAIL)
    raise SystemExit(1 if FAIL else 0)
