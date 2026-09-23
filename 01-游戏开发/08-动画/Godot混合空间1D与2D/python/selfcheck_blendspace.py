"""Godot 混合空间自检：期望值逐条手算过。"""

import math

from blendspace import (
    BlendSpace1D, BlendSpace2D, INTERPOLATED, DISCRETE,
    SYNC_NONE, SYNC_CYCLIC_MUTABLE, SYNC_FIXED,
)

COUNT = 0
FAIL = []


def ok(cond, msg):
    global COUNT
    COUNT += 1
    if not cond:
        FAIL.append(msg)
        print("FAIL:", msg)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def wclose(w, exp, tol=1e-9):
    return len(w) == len(exp) and all(close(x, y, tol) for x, y in zip(w, exp))


# ---------------------------------------------------------------- 1D 插值
bs = BlendSpace1D([0.0, 1.0, 2.0])
r = bs.process(0.5)
ok(wclose(r["weights"], [0.5, 0.5, 0.0]), f"0.5 应在 0 与 1 之间对半, 实得 {r['weights']}")
ok(r["closest"] == 1, f"权重相等时 closest 取下标更大者（>= 判定）, 实得 {r['closest']}")
r = bs.process(1.5)
ok(wclose(r["weights"], [0.0, 0.5, 0.5]), f"1.5 应在 1 与 2 之间对半, 实得 {r['weights']}")
r = bs.process(1.0)
ok(wclose(r["weights"], [0.0, 1.0, 0.0]), f"正好落在点位上, 实得 {r['weights']}")
r = bs.process(-5.0)
ok(wclose(r["weights"], [1.0, 0.0, 0.0]), f"低于所有点位 → 只有 higher 侧, 实得 {r['weights']}")
r = bs.process(9.0)
ok(wclose(r["weights"], [0.0, 0.0, 1.0]), f"高于所有点位 → 只有 lower 侧, 实得 {r['weights']}")

# 点位乱序不影响结果（源码是线性扫描）
bs_u = BlendSpace1D([2.0, 0.0, 1.0])
ok(wclose(bs_u.process(0.5)["weights"], [0.0, 0.5, 0.5]),
   "乱序点位下 0.5 仍在 0 与 1 之间对半")

# 非均匀间距：points 0 与 3
bs_n = BlendSpace1D([0.0, 3.0])
ok(wclose(bs_n.process(1.0)["weights"], [2.0 / 3.0, 1.0 / 3.0]),
   f"0 与 3 之间取 1 → 2/3 : 1/3, 实得 {bs_n.process(1.0)['weights']}")

# 单点：直接给 1.0
ok(wclose(BlendSpace1D([7.0]).process(0.0)["weights"], [1.0]), "只有一个混合点时权重恒为 1")
ok(BlendSpace1D([7.0]).process(0.0)["closest"] == 0, "单点时 closest = 0")

# ---------------------------------------------------------------- 1D 离散
bsd = BlendSpace1D([-1.0, 1.0], blend_mode=DISCRETE)
ok(bsd.process(0.0)["closest"] == 0,
   f"离散模式下距离相等时取下标更小者（严格 < 判定）, 实得 {bsd.process(0.0)['closest']}")
ok(wclose(bsd.process(0.0)["weights"], [1.0, 0.0]), "离散模式权重是 one-hot")
ok(bsd.process(0.9)["closest"] == 1, "离散模式 0.9 更靠近 1.0")
bsd3 = BlendSpace1D([0.0, 5.0, 6.0], blend_mode=DISCRETE)
ok(bsd3.process(5.4)["closest"] == 1, "离散模式 5.4 更靠近 5.0")
ok(bsd3.process(5.6)["closest"] == 2, "离散模式 5.6 更靠近 6.0")
ok(bsd3.process(-100.0)["closest"] == 0, "远离区间时取最近的端点")

# 插值 vs 离散 的平局取向相反（成对断言）
tie_i = BlendSpace1D([-1.0, 1.0], blend_mode=INTERPOLATED)
ok(tie_i.process(0.0)["closest"] == 1 and bsd.process(0.0)["closest"] == 0,
   "同一配置下：插值取下标大者、离散取下标小者")

# ---------------------------------------------------------------- 1D sync
bss = BlendSpace1D([0.0, 1.0], lengths=[2.0, 1.0], sync_mode=SYNC_CYCLIC_MUTABLE)
r = bss.process(0.5)
ok(close(r["delta_scale"], 1.0 / 1.5, 1e-9),
   f"sync 目标长度 = 加权平均 1.5 → 缩放 1/1.5, 实得 {r['delta_scale']}")
r = bss.process(0.0)
ok(close(r["delta_scale"], 1.0 / 2.0), f"权重全在 2.0 长度的点上 → 1/2, 实得 {r['delta_scale']}")
bss0 = BlendSpace1D([0.0, 1.0], lengths=[0.0, 1.0], sync_mode=SYNC_CYCLIC_MUTABLE)
ok(close(bss0.process(0.5)["delta_scale"], 1.0 / 1.0),
   "长度为 0 的点被排除（<= CMP_EPSILON）→ 目标长度取剩下那个")
bssf = BlendSpace1D([0.0, 1.0], lengths=[2.0, 1.0], sync_mode=SYNC_FIXED, cyclic_length=4.0)
ok(close(bssf.process(0.5)["delta_scale"], 0.25), "FIXED 模式用用户指定的 cyclic_length")
ok(BlendSpace1D([0.0, 1.0], lengths=[2.0, 1.0], sync_mode=SYNC_NONE).process(0.5)["delta_scale"] is None,
   "SYNC_NONE 时不给时间缩放（源码 deltas[i] = NAN）")

# ---------------------------------------------------------------- 2D 重心坐标
tri = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
ok(wclose(BlendSpace2D.blend_triangle((0.25, 0.25), tri), [0.5, 0.25, 0.25]),
   f"(0.25,0.25) 的重心坐标应为 (0.5,0.25,0.25), 实得 {BlendSpace2D.blend_triangle((0.25, 0.25), tri)}")
ok(wclose(BlendSpace2D.blend_triangle((0.0, 0.0), tri), [1.0, 0.0, 0.0]), "顶点 0 精确命中")
ok(wclose(BlendSpace2D.blend_triangle((1.0, 0.0), tri), [0.0, 1.0, 0.0]), "顶点 1 精确命中")
ok(wclose(BlendSpace2D.blend_triangle((0.0, 1.0), tri), [0.0, 0.0, 1.0]), "顶点 2 精确命中")
cen = (1.0 / 3.0, 1.0 / 3.0)
ok(wclose(BlendSpace2D.blend_triangle(cen, tri), [1.0 / 3.0] * 3, 1e-9),
   "重心处三个权重各 1/3")
ok(wclose(BlendSpace2D.blend_triangle((0.5, 0.5), ((0.0, 0.0), (2.0, 0.0), (1.0, 0.0))),
          [1.0, 0.0, 0.0]), "三点共线（denom == 0）时退化成 [1,0,0]")
ok(close(sum(BlendSpace2D.blend_triangle((0.2, 0.3), tri)), 1.0), "重心坐标恒和为 1")

# ---------------------------------------------------------------- 2D 处理
bs2 = BlendSpace2D([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], triangles=[(0, 1, 2)])
r = bs2.process((0.25, 0.25))
ok(wclose(r["weights"], [0.5, 0.25, 0.25]), f"2D 内部点按重心坐标给权重, 实得 {r['weights']}")
ok(r["triangle"] == 0, "命中第一个三角形")
ok(r["closest"] == 0, f"权重最大者是顶点 0（0.5）, 实得 {r['closest']}")
r = bs2.process((0.7, 0.2))
ok(close(sum(r["weights"]), 1.0), "权重和恒为 1")
ok(close(r["weights"][1], 0.7, 1e-9) and close(r["weights"][2], 0.2, 1e-9),
   f"(0.7,0.2) 的重心坐标应为 (0.1,0.7,0.2), 实得 {r['weights']}")

# 点在三角形外 → 投影到最近的边
r = bs2.process((2.0, 0.0))
ok(close(sum(r["weights"]), 1.0), "外部点的权重和仍为 1")
ok(r["weights"][2] == 0.0, "落在顶点 0-1 那条边上时第三个顶点权重为 0")
ok(close(r["weights"][1], 1.0), f"(2,0) 投影到顶点 1 → 权重 [0,1,0], 实得 {r['weights']}")
r = bs2.process((0.0, -1.0))
ok(close(r["weights"][0], 1.0), f"(0,-1) 投影到顶点 0 → 权重 [1,0,0], 实得 {r['weights']}")
r = bs2.process((1.0, 1.0))
ok(close(r["weights"][1], 0.5) and close(r["weights"][2], 0.5),
   f"(1,1) 投影到 1-2 边中点 → [0,0.5,0.5], 实得 {r['weights']}")

# 无三角形时插值模式不产生权重
ok(sum(BlendSpace2D([(0.0, 0.0), (1.0, 0.0)], triangles=[]).process((0.5, 0.0))["weights"]) == 0.0,
   "没有三角形时插值模式直接返回空权重")

# ---------------------------------------------------------------- 2D 离散
bs2d = BlendSpace2D([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], blend_mode=DISCRETE)
ok(bs2d.process((0.4, 0.4))["closest"] == 0,
   "(0.4,0.4) 离 (0,0) 最近（平方距离 0.32 < 0.52）")
ok(bs2d.process((1.0, 1.0))["closest"] == 1,
   f"(1,1) 到 (1,0) 与 (0,1) 距离相同（都是 1）→ 取下标更小者, 实得 {bs2d.process((1.0, 1.0))['closest']}")
ok(bs2d.process((0.9, 0.0))["closest"] == 1, "离散 2D：更靠近 (1,0)")
ok(wclose(bs2d.process((-5.0, -5.0))["weights"], [1.0, 0.0, 0.0]), "离散 2D：权重 one-hot")

# ---------------------------------------------------------------- 不变量
for pos in (0.0, 0.33, 0.5, 0.77, 1.0, 2.0):
    rr = bs.process(pos)
    ok(close(sum(rr["weights"]), 1.0, 1e-12), f"1D 权重和在 {pos} 处应为 1")
    ok(all(w >= -1e-12 for w in rr["weights"]), f"1D 权重非负（{pos}）")
for p in ((0.1, 0.1), (0.9, 0.05), (0.4, 0.4)):
    rr = bs2.process(p)
    ok(close(sum(rr["weights"]), 1.0, 1e-12), f"2D 权重和在 {p} 处应为 1")

print(f"\n断言总数: {COUNT}, 失败: {len(FAIL)}")
if FAIL:
    for m in FAIL:
        print("  -", m)
    raise SystemExit(1)
print("全部通过")
