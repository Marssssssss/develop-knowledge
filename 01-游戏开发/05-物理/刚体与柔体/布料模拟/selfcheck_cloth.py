"""布料模拟自检 —— 约束构建 + 两套弯曲模型对照 + 子步效应，全部实跑。"""

from math import pi, acos

from main import (
    Cloth, make_grid, find_tri_neighbors, find_tri_neighbors_robust,
    vadd, vsub, vmul, vdot, vlen, vnorm, vcross,
)

PASS = 0


def ok(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1


def close(a, b, tol=1e-9, msg=""):
    ok(abs(a - b) <= tol, "%s (got %r want %r)" % (msg, a, b))


# ==================== 1. findTriNeighbors：公共边配对 ====================
# 一个四边形由两个三角形拼成：共享中间那条对角线
tris = [0, 1, 2, 1, 3, 2]
nbr = find_tri_neighbors(tris)
# 边号：0=(0,1) 1=(1,2) 2=(2,0) | 3=(1,3) 4=(3,2) 5=(2,1)
ok(nbr[1] == 5 and nbr[5] == 1, "共享边 (1,2) 与 (2,1) 配对（%r）" % (nbr,))
ok(nbr[0] == -1 and nbr[2] == -1, "开放边为 -1")
ok(nbr[3] == -1 and nbr[4] == -1, "另一侧的开放边也是 -1")
# 单个三角形：三条边全是开放边
ok(find_tri_neighbors([0, 1, 2]) == [-1, -1, -1], "孤立的三角形没有邻居")

# ==================== 2. 约束构建：边去重 + 弯曲取对角 ====================
pos, tris = make_grid(3, 3, dx=0.1, dy=0.1)     # 3x3 顶点 = 4 个格子 = 8 个三角形

# 唯一边数：横向 3行×2 + 纵向 3列×2 + 每格 1 条对角线 = 6 + 6 + 4 = 16
unique_edges = set()
for i in range(len(tris) // 3):
    for j in range(3):
        a = tris[3 * i + j]
        b = tris[3 * i + (j + 1) % 3]
        unique_edges.add((min(a, b), max(a, b)))
ok(len(unique_edges) == 16, "3x3 网格的唯一边数是 16（实得 %d）" % len(unique_edges))

# --- 官方相邻三角形查找的实测缺陷 ---
nbr_official = find_tri_neighbors(tris)
nbr_robust = find_tri_neighbors_robust(tris)
shared_official = sum(1 for x in nbr_official if x >= 0)
shared_robust = sum(1 for x in nbr_robust if x >= 0)
ok(shared_robust == 16, "修正版配出 16 个条目（8 条共享边 × 2）")
ok(shared_official == 2, "官方版只配出 2 个条目（1 条共享边）—— 实测漏配")
ok(shared_official < shared_robust,
   "官方'排序后两两扫'会漏配跨偶数/奇数边界的重复边（%d < %d）"
   % (shared_official, shared_robust))

# --- 漏配的直接后果：拉伸约束重复登记 ---
cloth_official = Cloth(pos, tris, density=1.0)
cloth = Cloth(pos, tris, density=1.0, neighbor_finder=find_tri_neighbors_robust)
ok(len(cloth.stretch_ids) == 16,
   "用修正版：拉伸约束数 == 唯一边数 16（实得 %d）" % len(cloth.stretch_ids))
ok(len(cloth_official.stretch_ids) == 23,
   "用官方版：同一条边被登记两次，约束数变成 23（实得 %d）"
   % len(cloth_official.stretch_ids))
ok(len(cloth_official.bend_ids) < len(cloth.bend_ids),
   "漏配还让弯曲约束变少（%d < %d）"
   % (len(cloth_official.bend_ids), len(cloth.bend_ids)))

seen = set()
for a, b in cloth.stretch_ids:
    key = (min(a, b), max(a, b))
    ok(key not in seen, "边 (%d,%d) 没有重复登记" % (a, b))
    seen.add(key)
ok(len(seen) == len(cloth.stretch_ids), "每条边只出现一次")
ok(len(cloth.bend_ids) > 0, "存在弯曲约束（%d 组）" % len(cloth.bend_ids))
for (i0, i1, i2, i3) in cloth.bend_ids:
    ok(len({i0, i1, i2, i3}) == 4, "弯曲约束的四个顶点互不相同")
    ok({i0, i1}.isdisjoint({i2, i3}), "id2/id3 是两个三角形各自的**对角**顶点")

# ==================== 3. 质量：顶点 = 相邻三角形质量的 1/3 之和 ====================
pos2, tris2 = make_grid(3, 3, dx=0.1, dy=0.1)
c2 = Cloth(pos2, tris2, density=2.0, neighbor_finder=find_tri_neighbors_robust)
# 每个格子面积 = 0.1*0.1 = 0.01，两个三角形各 0.005
# 角上顶点属于 2 个三角形，边上属于 4 个，中心属于 6 个
area_tri = 0.1 * 0.1 / 2.0
tri_mass = 2.0 * area_tri
# 逐顶点核对：质量 = 相邻三角形数 × 每三角形的 1/3
for v in range(c2.num_particles):
    cnt = sum(1 for t in range(len(tris2) // 3) if v in tris2[3 * t:3 * t + 3])
    close(c2.mass[v], cnt * tri_mass / 3.0, tol=1e-12,
          msg="顶点 %d 的质量 = %d 个相邻三角形 × 1/3" % (v, cnt))
ok(c2.mass[4] > c2.mass[0], "中心顶点比角上顶点重（%.5f > %.5f）" % (c2.mass[4], c2.mass[0]))
close(c2.total_mass(), 8 * tri_mass, tol=1e-12,
      msg="总质量 = 8 个三角形质量之和（每个三角形 3 份 1/3 正好守恒）")

# ==================== 4. 固定点（逆质量置 0）永不移动 ====================
pos3, tris3 = make_grid(4, 4, dx=0.1, dy=0.1, y0=1.0)
c3 = Cloth(pos3, tris3, pin_ids=(0, 3), neighbor_finder=find_tri_neighbors_robust)
pinned0 = c3.pos[0]
pinned3 = c3.pos[3]
ok(c3.inv_mass[0] == 0.0 and c3.inv_mass[3] == 0.0, "固定点逆质量为 0")
for _ in range(200):
    c3.simulate(1.0 / 60.0, substeps=15)
ok(c3.pos[0] == pinned0, "跑了 200 帧后左上固定点纹丝不动")
ok(c3.pos[3] == pinned3, "跑了 200 帧后右上固定点纹丝不动")
ok(0.69 < c3.pos[15][1] <= 0.7001,
   "布初始就是绷紧的：不可伸长时底角最多停在 0.7（实得 %.4f）" % c3.pos[15][1])

# ==================== 5. 拉伸柔度：0 时几乎不可伸长 ====================
def run(stretch_c, substeps=15, frames=120):
    p, t = make_grid(4, 4, dx=0.1, dy=0.1, y0=1.0)
    c = Cloth(p, t, stretching_compliance=stretch_c, bending_compliance=1.0,
              pin_ids=(0, 3), neighbor_finder=find_tri_neighbors_robust)
    for _ in range(frames):
        c.simulate(1.0 / 60.0, substeps=substeps)
    return c


inext = run(0.0)
soft = run(1e-2)
ok(inext.max_stretch_error() < 1e-3,
   "stretchingCompliance=0：最大拉伸误差 %.3e 接近 0（布不可伸长）"
   % inext.max_stretch_error())
ok(soft.max_stretch_error() > inext.max_stretch_error(),
   "柔度 1e-2 时拉伸误差明显更大（%.3e > %.3e）"
   % (soft.max_stretch_error(), inext.max_stretch_error()))
# 可观测后果：软布会真的垂得更低
ok(min(p[1] for p in soft.pos) < min(p[1] for p in inext.pos),
   "软布的底边比不可伸长布更低（%.4f < %.4f）"
   % (min(p[1] for p in soft.pos), min(p[1] for p in inext.pos)))

# ==================== 6. 弯曲柔度：控制"抗折"程度 ====================
def run_bend(bend_c, frames=120):
    p, t = make_grid(4, 4, dx=0.1, dy=0.1, y0=1.0)
    c = Cloth(p, t, stretching_compliance=0.0, bending_compliance=bend_c,
              pin_ids=(0, 3), neighbor_finder=find_tri_neighbors_robust)
    for _ in range(frames):
        c.simulate(1.0 / 60.0, substeps=15)
    return c


stiff_bend = run_bend(0.0)
soft_bend = run_bend(1e3)
ok(stiff_bend.max_bend_error() < soft_bend.max_bend_error(),
   "弯曲柔度 0 比 1e3 更能维持对角长度（%.3e < %.3e）"
   % (stiff_bend.max_bend_error(), soft_bend.max_bend_error()))

# ==================== 7. 子步数量：越多越收敛 ====================
errs = []
for n in (1, 5, 15):
    c = run(0.0, substeps=n, frames=60)
    errs.append(c.max_stretch_error())
ok(errs[0] > errs[1] > errs[2],
   "子步 1 > 5 > 15 的拉伸误差单调下降（%.3e > %.3e > %.3e）" % tuple(errs))
ok(errs[2] < errs[0] / 2.0, "15 个子步比 1 个子步误差小一半以上")

# ==================== 8. 地面碰撞是 preSolve 里的"位置投影" ====================
p8, t8 = make_grid(3, 3, dx=0.1, dy=0.1, y0=1.0)
c8 = Cloth(p8, t8, neighbor_finder=find_tri_neighbors_robust)   # 不固定，整块布自由下落
for _ in range(400):
    c8.simulate(1.0 / 60.0, substeps=15)
# 3x3 网格自身尺寸是 0.2×0.2，落到地面后整块摊平，最高点应≈0.2（布的自身厚度级）
ok(max(p[1] for p in c8.pos) < 0.25, "布最终摊在地面上（最高点 y = %.4f）"
   % max(p[1] for p in c8.pos))
ok(min(p[1] for p in c8.pos) < 1e-6, "布确实落到了地面（最低点 y = %.4e）"
   % min(p[1] for p in c8.pos))
# **实测的坑**：地面只在 preSolve 处理，随后的约束求解可以把点重新拽到地面以下
dip = min(p[1] for p in c8.pos)
ok(dip < 0.0, "约束求解之后确实有顶点略微陷到地面以下（%.3e）" % dip)
ok(dip > -1e-3, "但这个穿透量很小（%.3e，约格距的万分之六）" % dip)
# 单独验 pre_solve 的投影：人为把一点放到地下
c8.pos[0] = (c8.pos[0][0], -0.5, c8.pos[0][2])
c8.prev[0] = (c8.pos[0][0], 0.02, c8.pos[0][2])
c8.pre_solve(1.0 / 60.0)
close(c8.pos[0][1], 0.0, tol=1e-12, msg="pre_solve 把地面以下的点直接投影回 y=0")

# ==================== 9. 二面角弯曲（PBD 2006 §4.1）与拉伸解耦 ====================
p9, t9 = make_grid(4, 4, dx=0.1, dy=0.1)
c9 = Cloth(p9, t9, pin_ids=(0, 3), neighbor_finder=find_tri_neighbors_robust)
c9._init_dihedral_rest()
ok(len(c9._dihedral_rest) == len(c9.bend_ids), "每对相邻三角形一个静止二面角")
# 口径说明：本实现按 PBD 2006 的公式取 n1 = (p2-p1)×(p3-p1)、n2 = (p2-p1)×(p4-p1)，
# 两个"对角顶点" p3 与 p4 落在共享边 p1-p2 的两侧，故**平铺**时 n1·n2 = -1，
# 静止二面角是 π（不是 0）。这是公式本身决定的，不是 bug。
close(c9._dihedral_rest[0], pi, tol=1e-9, msg="平铺布面的静止二面角是 π")
# 人为折一下：把某个顶点抬起来制造非零二面角
fold = list(c9.pos)
fold[5] = (fold[5][0], fold[5][1], 0.05)
c9.pos = fold
folded_phi = c9._phi_of(0)
ok(abs(folded_phi - pi) > 1e-6,
   "折起来之后二面角偏离 π（%.6f -> 偏离 %.6f）" % (folded_phi, abs(folded_phi - pi)))
# 用二面角求解器把它拉回去
before = abs(c9._phi_of(0) - c9._dihedral_rest[0])
for _ in range(50):
    c9.solve_dihedral(1.0 / 60.0, stiffness=1.0)
after = abs(c9._phi_of(0) - c9._dihedral_rest[0])
ok(after < before, "二面角弯曲求解把折叠角拉回静止角（%.6f -> %.6f）" % (before, after))

# ==================== 10. 动量守恒：内部约束不该让布自己飞起来 ====================
p10, t10 = make_grid(3, 3, dx=0.1, dy=0.1)
c10 = Cloth(p10, t10, neighbor_finder=find_tri_neighbors_robust)


def centroid(c):
    m = c.total_mass()
    return vmul((sum(c.mass[i] * c.pos[i][0] for i in range(c.num_particles)),
                 sum(c.mass[i] * c.pos[i][1] for i in range(c.num_particles)),
                 sum(c.mass[i] * c.pos[i][2] for i in range(c.num_particles))), 1.0 / m)


before = centroid(c10)
snap = list(c10.pos)
for _ in range(30):
    c10.solve(1.0 / 60.0)
after = centroid(c10)
drift = vlen(vsub(after, before))
ok(drift < 1e-9, "纯约束求解不移动质心（约束是内部的，漂移 %.3e）" % drift)
moved = sum(1 for i in range(c10.num_particles) if c10.pos[i] != snap[i])
ok(moved == 0, "平铺布已经是约束的满足态，求解不改变任何顶点（%d 个动了）" % moved)

print("布料模拟: %d 项断言全部通过" % PASS)
