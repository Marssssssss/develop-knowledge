"""SAT 与 EPA —— 凸形状相交判定、最小平移向量(MTV)与穿透深度。

判据与公式全部取自下列**实读**资料，代码刻意与原文伪代码保持同构：

* dyn4j《SAT (Separating Axis Theorem)》
  - "If two convex objects are not penetrating, there exists an axis for which
    the projection of the objects will not overlap."
  - 待检轴 = 两个形状各自**边法线**（翻转坐标再取反一个分量：`(x,y) -> (-y,x)`）；
    原文强调若只要布尔结果可不归一化，要拿到 MTV **必须归一化**。
  - 投影 = 顶点与轴点积的 min/max。
  - MTV = 重叠量最小的那根轴 + 该重叠量。
  - 平行轴可去重（矩形实际只有 2 根轴）。
  - 圆-圆只测 1 根轴（圆心连线）；圆-多边形要**额外**补一根"最近顶点 -> 圆心"的轴。
  - 包含(containment)特例：纯最小重叠给出的 MTV **不够把形状推开**，
    原文做法是 `o += min(|p1.min-p2.min|, |p1.max-p2.max|)`，并提示可能要**反向**该轴。
* dyn4j《EPA (Expanding Polytope Algorithm)》
  - GJK 判出相交后，用 Minkowski **差**（不是和）上的终止单纯形继续扩张；
    2D 需要**完整的三角形**单纯形，3D 需要四面体。
  - 每轮取距原点最近的那条边，用三重积 `n = (e × oa) × e = oa(e·e) - e(oa·e)` 求外向法线，
    沿该法线取支撑点 `p`，若 `p·n - e.distance < TOLERANCE` 即收敛，`depth = p·n`。
  - 三重积在原点贴着边时会退化成零向量（归一化除零），原文给出改用保 winding 的替代求法。
  - 官方算例：单纯形 `(4,2) (-8,-2) (-1,-2)` → 最近边距离 **0.62**、
    法线 `(-0.32, 0.95)`；插入 `(-6,9)` 后最近边距离 **0.94**、法线 `(0.62,-0.78)`；
    支撑点 `(4,2)` 的 `p·n = 0.92`，`0.92 - 0.94 = -0.02 < tol` → 法线 `(0.62,-0.78)`、深度 **0.92**。

本文件只放模型，断言在 `selfcheck_sat_epa.py`。
"""

from math import sqrt

EPS = 1e-12


# ---------- 二维向量（元组表示，避免任何第三方依赖） ----------

def add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def mul(a, s):
    return (a[0] * s, a[1] * s)


def neg(a):
    return (-a[0], -a[1])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def perp(a):
    """原文 perp：`(x, y) => (-y, x)`。"""
    return (-a[1], a[0])


def length(a):
    return sqrt(dot(a, a))


def norm(a):
    l = length(a)
    return (a[0] / l, a[1] / l)


def center(poly):
    n = len(poly)
    return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)


def triple_product(a, b, c):
    """`(a x b) x c = b(a·c) - a(b·c)`，EPA 求边法线用。"""
    return sub(mul(b, dot(a, c)), mul(a, dot(b, c)))


# ---------- SAT ----------

def get_axes(poly):
    """原文的 getAxes：`edge = p1 - p2`，再取 perp 并归一化。"""
    axes = []
    n = len(poly)
    for i in range(n):
        p1 = poly[i]
        p2 = poly[(i + 1) % n]
        axes.append(norm(perp(sub(p1, p2))))
    return axes


def dedupe_axes(axes, tol=1e-9):
    """平行轴去重 —— 原文"可以减少待检轴数量"，矩形因此只剩 2 根。"""
    out = []
    for a in axes:
        if not any(abs(dot(a, b)) > 1 - tol for b in out):
            out.append(a)
    return out


def project(poly, axis):
    """原文 project：顶点点积的 min/max（轴必须已归一化）。"""
    mn = dot(axis, poly[0])
    mx = mn
    for i in range(1, len(poly)):
        p = dot(axis, poly[i])
        if p < mn:
            mn = p
        elif p > mx:
            mx = p
    return (mn, mx)


def overlap_amount(p1, p2, use_containment=True):
    """返回 `(overlap, contains)`；不相交时 `overlap` 为 None。

    `use_containment=False` 关掉原文的包含修正，用于演示"不修正会得到不够用的 MTV"。
    """
    if p1[1] < p2[0] or p2[1] < p1[0]:
        return (None, False)
    o = min(p1[1], p2[1]) - max(p1[0], p2[0])
    contains = ((p1[0] <= p2[0] and p2[1] <= p1[1]) or
                (p2[0] <= p1[0] and p1[1] <= p2[1]))
    if contains and use_containment:
        mins = abs(p1[0] - p2[0])
        maxs = abs(p1[1] - p2[1])
        o += mins if mins < maxs else maxs
    return (o, contains)


def sat(a, b, reduce_parallel=False, use_containment=True):
    """多边形-多边形 SAT。返回 None 或 `(mtv_axis, depth, n_axes_tested)`。"""
    axes = get_axes(a) + get_axes(b)
    if reduce_parallel:
        axes = dedupe_axes(axes)
    smallest = None
    best = None
    for axis in axes:
        o, _ = overlap_amount(project(a, axis), project(b, axis), use_containment)
        if o is None:
            return None
        if best is None or o < best:
            best = o
            smallest = axis
    # 原文提示：按方向可能需要把轴反过来 —— 这里统一指成 "从 a 指向 b"
    if dot(sub(center(b), center(a)), smallest) < 0:
        smallest = neg(smallest)
    return (smallest, best, len(axes))


def sat_circle_circle(c1, r1, c2, r2):
    """圆-圆：唯一待检轴是圆心连线（原文 Circle vs Circle）。"""
    axis = sub(c1, c2)
    if length(axis) < EPS:
        return ((1.0, 0.0), r1 + r2, 1)  # 同心：任取方向，深度为半径和
    axis = norm(axis)
    d = length(sub(c1, c2))
    if d >= r1 + r2:
        return None
    # 投影区间即 [c-r, c+r]，重叠量就是 r1+r2-d
    return (axis, r1 + r2 - d, 1)


def sat_circle_polygon(c, r, poly, extra_axis=True):
    """圆-多边形：多边形边法线 + "最近顶点 -> 圆心" 那根额外轴。

    `extra_axis=False` 关掉额外轴，用于演示原文说的
    "the center to center test along with the polygon axes is not enough"。
    """
    axes = get_axes(poly)
    best_v = poly[0]
    best_d = length(sub(poly[0], c))
    for p in poly[1:]:
        d = length(sub(p, c))
        if d < best_d:
            best_d = d
            best_v = p
    if extra_axis:
        extra = sub(c, best_v)
        if length(extra) > EPS:
            axes = axes + [norm(extra)]

    def project_circle(axis):
        s = dot(c, axis)
        return (s - r, s + r)

    smallest = None
    best = None
    for axis in axes:
        o, _ = overlap_amount(project_circle(axis), project(poly, axis))
        if o is None:
            return None
        if best is None or o < best:
            best = o
            smallest = axis
    if dot(sub(center(poly), c), smallest) < 0:
        smallest = neg(smallest)
    return (smallest, best, len(axes))


# ---------- Minkowski 支撑函数 ----------

def make_support(a, b):
    def farthest(poly, d):
        best = poly[0]
        bv = dot(poly[0], d)
        for p in poly[1:]:
            v = dot(p, d)
            if v > bv:
                bv = v
                best = p
        return best

    def support(d):
        return sub(farthest(a, d), farthest(b, neg(d)))

    return support


# ---------- GJK（只为给 EPA 提供终止单纯形） ----------

def gjk(support, start_dir=None, max_iter=64):
    """2D GJK：返回 None（不相交）或包含原点的三角形单纯形。"""
    d = start_dir if start_dir else (1.0, 0.0)
    simplex = [support(d)]
    d = neg(simplex[0])
    for _ in range(max_iter):
        p = support(d)
        if dot(p, d) < 0:
            return None
        simplex.append(p)
        if len(simplex) == 2:
            a, b = simplex
            ab = sub(b, a)
            ao = neg(a)
            d = triple_product(ab, ao, ab)
            if dot(d, d) < EPS:
                d = perp(ab)
                if dot(d, ao) < 0:
                    d = neg(d)
        else:
            a = simplex[-1]
            b = simplex[-2]
            c = simplex[-3]
            ab = sub(b, a)
            ac = sub(c, a)
            ao = neg(a)
            abp = triple_product(ac, ab, ab)
            if dot(abp, ao) > 0:
                simplex = [a, b]
                d = abp
            else:
                acp = triple_product(ab, ac, ac)
                if dot(acp, ao) > 0:
                    simplex = [a, c]
                    d = acp
                else:
                    return simplex
    return None


# ---------- EPA ----------

def find_closest_edge(simplex):
    """原文 findClosestEdge：返回 `(distance, normal, insert_index)`。"""
    closest = None
    for i in range(len(simplex)):
        j = (i + 1) % len(simplex)
        a = simplex[i]
        b = simplex[j]
        e = sub(b, a)
        n = norm(triple_product(e, a, e))
        d = dot(n, a)
        if closest is None or d < closest[0]:
            closest = (d, n, j)
    return closest


def find_closest_edge_winding_safe(simplex):
    """原文 Winding 一节的三重积替代求法：

    当原点几乎贴在边上时三重积会退化成零向量。保 winding 的写法是先求边的
    外法线，再按 winding 决定符号、并保证它指向原点外侧。
    """
    closest = None
    for i in range(len(simplex)):
        j = (i + 1) % len(simplex)
        a = simplex[i]
        b = simplex[j]
        e = sub(b, a)
        cand = norm(perp(e))
        if dot(cand, a) < 0:   # 强制法线指向原点一侧（与三重积写法同向）
            cand = neg(cand)
        d = dot(cand, a)
        if closest is None or d < closest[0]:
            closest = (d, cand, j)
    return closest


def epa(support, simplex, tolerance=1e-5, max_iter=64, closest_edge=find_closest_edge):
    """原文扩张循环。返回 `(normal, depth, iterations, final_size)`，不收敛返回 None。"""
    simplex = list(simplex)
    for it in range(1, max_iter + 1):
        d, n, idx = closest_edge(simplex)
        p = support(n)
        proj = dot(p, n)
        if proj - d < tolerance:
            return (n, proj, it, len(simplex))
        simplex.insert(idx, p)
    return None
