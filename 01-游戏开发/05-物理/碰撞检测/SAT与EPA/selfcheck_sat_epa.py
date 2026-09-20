"""SAT 与 EPA 自检 —— 全部断言实跑。

断言风格遵循"验到"而不是"应然"：
* 官方算例用**精确值**重算（原文写作 0.62 / 0.92 是因为用了四舍五入后的分量）；
* 包含修正用"按 MTV 推开后是否还相交"来验，而不是断言某个数字对；
* EPA 与 SAT 用**互相独立**的两条路径对拍（GJK+EPA vs 投影求最小重叠）。
"""

from main import (
    get_axes, dedupe_axes, project, overlap_amount, sat,
    sat_circle_circle, sat_circle_polygon,
    make_support, gjk, epa,
    find_closest_edge, find_closest_edge_winding_safe,
    dot, sub, add, mul, norm, length, neg,
)

PASS = 0


def ok(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1


def close(a, b, tol=1e-9, msg=""):
    ok(abs(a - b) <= tol, "%s (got %r want %r)" % (msg, a, b))


def translate(poly, axis, d):
    return [add(p, mul(axis, d)) for p in poly]


# ---------------------------------------------------------------- 1. 待检轴
SQUARE = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
axes = get_axes(SQUARE)
ok(len(axes) == 4, "4 条边 -> 4 根轴")
for ax in axes:
    close(length(ax), 1.0, msg="轴必须归一化才能拿 MTV")
# 原文："a rectangle only has two axes to test"（平行轴去重）
close(len(dedupe_axes(axes)), 2, msg="矩形去重后只剩 2 根轴")
# 单位正方形：投影区间长度就是 1
p = project(SQUARE, (1.0, 0.0))
close(p[0], 0.0); close(p[1], 1.0)
p = project(SQUARE, (0.0, 1.0))
close(p[0], 0.0); close(p[1], 1.0)

# ------------------------------------------------------------ 2. 分离 / 相交
far = [(3.0, 0.0), (4.0, 0.0), (4.0, 1.0), (3.0, 1.0)]
ok(sat(SQUARE, far) is None, "相距 2 个单位 -> 不相交")

# 沿 x 重叠 0.5、沿 y 完全重合 -> MTV 是 x 轴，深度 0.5
shifted = [(0.5, 0.0), (1.5, 0.0), (1.5, 1.0), (0.5, 1.0)]
axis, depth, n = sat(SQUARE, shifted)
close(depth, 0.5, msg="重叠 0.5")
close(abs(dot(axis, (1.0, 0.0))), 1.0, msg="MTV 应沿 x 轴")
close(dot(axis, (1.0, 0.0)), 1.0, msg="MTV 方向被统一成 从 a 指向 b")

# ------------------------------------------------------ 3. 包含(containment)
BIG = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
SMALL = [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0)]
axis_n, depth_n, _ = sat(BIG, SMALL, use_containment=False)
axis_c, depth_c, _ = sat(BIG, SMALL, use_containment=True)
close(depth_n, 1.0, msg="不做包含修正时最小重叠是 1")
close(depth_c, 2.0, msg="包含修正后 MTV 深度是 2")
# 关键验到：按"不修正"的 MTV 推开，两个形状**仍然相交**
ok(sat(BIG, translate(SMALL, axis_n, depth_n + 1e-6)) is not None,
   "不做包含修正：推开后仍相交（MTV 不够用）")
# 按"修正后"的 MTV 推开，depth-eps 仍相交、depth+eps 分离
ok(sat(BIG, translate(SMALL, axis_c, depth_c - 1e-6)) is not None,
   "修正后：少推一点点仍相交")
ok(sat(BIG, translate(SMALL, axis_c, depth_c + 1e-6)) is None,
   "修正后：多推一点点就分离")

# ------------------------------------------------------------------ 4. 圆-圆
res = sat_circle_circle((0.0, 0.0), 1.0, (1.5, 0.0), 1.0)
ok(res is not None, "圆心距 1.5 < 半径和 2 -> 相交")
close(res[1], 0.5, msg="圆-圆穿透深度 = r1+r2-d")
close(res[2], 1, msg="圆-圆只测 1 根轴")
ok(sat_circle_circle((0.0, 0.0), 1.0, (2.5, 0.0), 1.0) is None, "圆心距 2.5 -> 分离")

# ------------------------------------------------------------- 5. 圆-多边形
# 圆心贴着正方形右上角外侧，半径小于到角的距离：只用多边形轴会**误判为相交**
c = (1.2, 1.2)
r = 0.2
corner_dist = length(sub(c, (1.0, 1.0)))
ok(corner_dist > r, "构造前提：圆心到角点的距离大于半径，实际不相交")
ok(sat_circle_polygon(c, r, SQUARE, extra_axis=False) is not None,
   "只用多边形边法线 -> 误报相交（原文所述缺陷）")
ok(sat_circle_polygon(c, r, SQUARE, extra_axis=True) is None,
   "补上'最近顶点->圆心'轴 -> 正确判为分离")
# 把半径放大到确实相交时，两种写法都应该报相交，且额外轴给出的是角点方向
big_r = 0.5
res = sat_circle_polygon(c, big_r, SQUARE, extra_axis=True)
ok(res is not None, "半径 0.5 时确实相交")
close(abs(dot(norm(res[0]), norm((0.7071067811865476, 0.7071067811865476)))),
      1.0, tol=1e-9, msg="MTV 沿角点方向")

# ------------------------------------------- 6. EPA 官方算例（dyn4j EPA 一节）
# 原文单纯形与两轮支撑点；精确值与原文四舍五入后的 0.62 / 0.94 / 0.92 对照
s1 = [(4.0, 2.0), (-8.0, -2.0), (-1.0, -2.0)]
d1, n1, i1 = find_closest_edge(s1)
close(d1, 0.6324555320336759, tol=1e-9, msg="第 1 轮最近边距离精确值（原文记 0.62）")
close(n1[0], -0.31622776601683794, tol=1e-12, msg="原文分量 -0.32")
close(n1[1], 0.9486832980505138, tol=1e-12, msg="原文分量 0.95")
ok(i1 == 1, "新点插在构成最近边的两点之间（index=j=1）")

# 用原文记录的两个支撑点构造 support：沿 n1 得 (-6,9)、沿第 2 轮法线得 (4,2)
n2_exact = norm((24.0, -30.0))


def article_support(d):
    if dot(d, n1) > 0.999999:
        return (-6.0, 9.0)
    if dot(d, n2_exact) > 0.999999:
        return (4.0, 2.0)
    raise AssertionError("unexpected direction %r" % (d,))


s2 = list(s1)
s2.insert(i1, article_support(n1))
ok(s2 == [(4.0, 2.0), (-6.0, 9.0), (-8.0, -2.0), (-1.0, -2.0)], "插入后单纯形与原文一致")
d2, n2, i2 = find_closest_edge(s2)
close(d2, 0.9370425713316364, tol=1e-9, msg="第 2 轮最近边距离精确值（原文记 0.94）")
close(n2[0], 0.6246950475544243, tol=1e-12, msg="原文分量 0.62")
close(n2[1], -0.7808688094430304, tol=1e-12, msg="原文分量 -0.78")
ok(i2 == 0, "第 2 轮插入下标为 0")

p = article_support(n2)
proj = dot(p, n2)
ok(proj - d2 < 1e-5, "收敛判据 p·n - distance < TOLERANCE 成立")
close(proj, 0.9370425713316364, tol=1e-9, msg="深度精确值（原文记 0.92）")

# 走完整 epa() 也应得到同一个结果
res = epa(article_support, s1, tolerance=1e-5)
ok(res is not None, "epa() 收敛")
close(res[1], 0.9370425713316364, tol=1e-9, msg="epa() 深度")
close(abs(dot(res[0], n2_exact)), 1.0, tol=1e-12, msg="epa() 法线")

# ------------------------------------- 7. 三重积退化：保 winding 替代求法
# 原点正好落在某条边的直线上时，三重积退化成零向量 -> 归一化除零
degenerate = [(1.0, 0.0), (-1.0, 0.0), (0.0, 2.0)]
raised = False
try:
    find_closest_edge(degenerate)
except ZeroDivisionError:
    raised = True
ok(raised, "三重积在退化边上产生零向量 -> ZeroDivisionError（原文所述除零风险）")
dw, nw, _ = find_closest_edge_winding_safe(degenerate)
close(dw, 0.0, tol=1e-12, msg="保 winding 写法不崩溃，该边距离为 0")
ok(length(nw) > 0.5, "保 winding 写法仍返回单位法线")
# 非退化情形下两种写法必须给出同一个最近边
for s in (s1, s2):
    da, na, _ = find_closest_edge(s)
    db, nb, _ = find_closest_edge_winding_safe(s)
    close(da, db, tol=1e-12, msg="两种写法距离一致")
    close(abs(dot(na, nb)), 1.0, tol=1e-12, msg="两种写法法线一致")

# ------------------------------------ 8. EPA 与 SAT 对拍（两条独立路径）
A = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
cases = [
    [(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)],   # 角落重叠 1x1
    [(1.5, 0.2), (2.5, 0.2), (2.5, 1.8), (1.5, 1.8)],   # 侧面插入
    [(0.6, 0.6), (1.4, 0.4), (1.6, 1.2), (0.8, 1.4)],   # 斜放四边形
]
degenerate_seen = 0
for B in cases:
    support = make_support(A, B)
    simplex = gjk(support, start_dir=norm(sub(
        ((sum(p[0] for p in A) / 4), (sum(p[1] for p in A) / 4)),
        ((sum(p[0] for p in B) / 4), (sum(p[1] for p in B) / 4)))))
    ok(simplex is not None, "GJK 判定相交")
    ok(len(simplex) == 3, "2D 需要完整三角形单纯形才能喂给 EPA")
    # 三重积写法在真实数据上会撞上退化边（原文所述除零风险确实会发生）
    try:
        epa(support, simplex, tolerance=1e-7, closest_edge=find_closest_edge)
    except ZeroDivisionError:
        degenerate_seen += 1
    # 保 winding 写法不崩溃，且与 SAT 的 MTV 对拍
    e_normal, e_depth, iters, size = epa(support, simplex, tolerance=1e-7,
                                         closest_edge=find_closest_edge_winding_safe)
    s_axis, s_depth, _ = sat(A, B)
    close(abs(dot(e_normal, s_axis)), 1.0, tol=1e-6, msg="EPA 法线与 SAT MTV 同轴")
    close(e_depth, s_depth, tol=1e-6, msg="EPA 深度与 SAT MTV 深度一致")
    ok(e_depth > 0, "穿透深度为正")
    ok(iters >= 1, "至少扩张一轮")
ok(degenerate_seen >= 1,
   "真实数据上至少有一个用例让三重积退化（该坑不是纸面假设，实测 %d 个）" % degenerate_seen)

# -------------------------------------------------- 9. 边界：仅接触不算相交
touching = [(2.0, 0.0), (3.0, 0.0), (3.0, 1.0), (2.0, 1.0)]
res = sat(A, touching)
ok(res is not None, "共边算相交（闭区间投影在此相交）")
close(res[1], 0.0, tol=1e-12, msg="MTV 深度为 0")

print("SAT与EPA: %d 项断言全部通过" % PASS)
