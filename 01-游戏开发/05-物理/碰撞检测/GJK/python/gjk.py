"""gjk.py — GJK (Gilbert-Johnson-Keerthi) 凸多边形碰撞检测最小实现（2D）。

核心原理：两凸形状 A、B 相交 <=> 它们的 Minkowski 差 A-B 包含原点。
GJK 不显式构造 A-B，而是通过 support 函数在其边界上取点，
迭代演化一个 simplex（点 -> 线段 -> 三角形），试探能否包围原点。

实现依据：dyn4j "Collision Detection for Convex Shapes" 教程的
simplex 演化 + Voronoi 区域判断法（含评论区修正：AB = B - A）。

运行：python gjk.py
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

MAX_ITER = 64    # 迭代上限：退化情况下防止死循环
EPS = 1e-12      # 零向量判定阈值


@dataclass(frozen=True)
class Vec2:
    x: float
    y: float


def dot(a: Vec2, b: Vec2) -> float:
    return a.x * b.x + a.y * b.y


def sub(a: Vec2, b: Vec2) -> Vec2:
    return Vec2(a.x - b.x, a.y - b.y)


def neg(a: Vec2) -> Vec2:
    return Vec2(-a.x, -a.y)


def triple_product(a: Vec2, b: Vec2, c: Vec2) -> Vec2:
    """三重积 (a x b) x c = b*(c·a) - a*(c·b)。

    用途：求“垂直于 b、且指向 c 一侧”的向量。
    """
    return sub(
        Vec2(b.x * dot(c, a), b.y * dot(c, a)),
        Vec2(a.x * dot(c, b), a.y * dot(c, b)),
    )


Polygon = Sequence[Vec2]


def support_poly(shape: Polygon, d: Vec2) -> Vec2:
    """support(shape, d)：返回形状在方向 d 上投影最大的顶点。

    GJK 唯一需要的几何接口，因此对圆/胶囊等曲边形状同样适用。
    """
    return max(shape, key=lambda p: dot(p, d))


def mink_support(a: Polygon, b: Polygon, d: Vec2) -> Vec2:
    """Minkowski 差上的 support 点：S_{A-B}(d) = S_A(d) - S_B(-d)。

    这样完全不需要构造 A-B 的全部点（不可行，点数是 |A|*|B|）。
    """
    return sub(support_poly(a, d), support_poly(b, neg(d)))


def centroid(shape: Polygon) -> Vec2:
    """顶点平均质心（凸多边形可用作初始方向的参考点）。"""
    n = len(shape)
    return Vec2(sum(p.x for p in shape) / n, sum(p.y for p in shape) / n)


def _handle_line(simplex: list[Vec2], d: list[Vec2]) -> bool:
    """线段情形：A 为最后加入点，B 为另一点。

    新方向取垂直于 AB 且指向原点一侧：d = (AB x AO) x AB。
    返回 True 表示已判定碰撞。
    """
    a, b = simplex[1], simplex[0]
    ab = sub(b, a)  # 注意：AB = B - A（dyn4j 评论区修正）
    ao = neg(a)     # AO = O - A = -A

    new_d = triple_product(ab, ao, ab)
    if dot(new_d, new_d) < EPS:
        # 原点恰好在 AB 线上：视为接触（这里按“包含边界即碰撞”处理）
        return True
    d[0] = new_d
    return False


def _handle_triangle(simplex: list[Vec2], d: list[Vec2]) -> bool:
    """三角形情形：通过 Voronoi 区域测试判断原点位置。

    - ab_perp·AO > 0：原点在 AB 外侧区域 -> 丢弃 C，朝 ab_perp 继续
    - ac_perp·AO > 0：原点在 AC 外侧区域 -> 丢弃 B，朝 ac_perp 继续
    - 否则：原点在三角形内部 -> 碰撞
    """
    a, b, c = simplex[2], simplex[1], simplex[0]
    ab = sub(b, a)
    ac = sub(c, a)
    ao = neg(a)

    ab_perp = triple_product(ac, ab, ab)  # 垂直 AB，背离 C
    ac_perp = triple_product(ab, ac, ac)  # 垂直 AC，背离 B

    if dot(ab_perp, ao) > 0:
        simplex[:] = [simplex[1], simplex[2]]  # 丢弃 C，保留 A、B
        d[0] = ab_perp
        return False
    if dot(ac_perp, ao) > 0:
        simplex[:] = [simplex[0], simplex[2]]  # 丢弃 B，保留 A、C
        d[0] = ac_perp
        return False
    return True  # 原点在三角形内 -> 相交


def gjk_intersect(a: Polygon, b: Polygon) -> tuple[bool, int]:
    """GJK 主循环。返回 (是否相交, 迭代次数)。"""
    # 初始方向任意；取中心连线利于尽早退出（dyn4j 建议）
    d = sub(centroid(b), centroid(a))
    if dot(d, d) < EPS:
        d = Vec2(1, 0)

    simplex = [mink_support(a, b, d)]
    d = neg(d)

    for iters in range(1, MAX_ITER):
        new_pt = mink_support(a, b, d)
        simplex.append(new_pt)

        # 关键终止条件 1：沿 d 方向的最远点都没有越过原点，
        # 说明整个 Minkowski 差在垂直 d 的直线一侧 -> 不含原点 -> 分离
        if dot(new_pt, d) <= 0:
            return False, iters

        # 终止条件 2：simplex 包含原点 -> 相交；否则演化 simplex
        box = [d]
        hit = _handle_line(simplex, box) if len(simplex) == 2 else _handle_triangle(simplex, box)
        d = box[0]
        if hit:
            return True, iters
    return False, MAX_ITER  # 浮点退化，保守返回分离


def run_case(name: str, a: Polygon, b: Polygon, expect: bool) -> None:
    hit, iters = gjk_intersect(a, b)
    status = "PASS" if hit == expect else "FAIL"
    print(f"{name:<28} -> {'COLLIDE' if hit else 'SEPARATE'} (iters={iters})"
          f"  expected={'COLLIDE' if expect else 'SEPARATE'}  [{status}]")


def main() -> None:
    sq_a = [Vec2(0, 0), Vec2(2, 0), Vec2(2, 2), Vec2(0, 2)]      # [0,2]x[0,2]
    sq_b1 = [Vec2(1, 1), Vec2(3, 1), Vec2(3, 3), Vec2(1, 3)]     # 与 A 重叠
    sq_b2 = [Vec2(5, 5), Vec2(7, 5), Vec2(7, 7), Vec2(5, 7)]     # 远离 A
    sq_b3 = [Vec2(3, 0), Vec2(5, 0), Vec2(5, 2), Vec2(3, 2)]     # 与 A 间隙 1
    sq_b4 = [Vec2(1.999, 1.9), Vec2(3.999, 1.9),
             Vec2(3.999, 3.9), Vec2(1.999, 3.9)]                 # 薄重叠
    pent = [Vec2(3, 1), Vec2(1.618, 2.618), Vec2(-0.618, 1.618),
            Vec2(-0.618, 0.382), Vec2(1.618, -0.618)]            # 五边形
    tri = [Vec2(2, 2), Vec2(4, 2), Vec2(3, 4)]                   # 三角形

    run_case("square vs square overlap", sq_a, sq_b1, True)
    run_case("square vs square far", sq_a, sq_b2, False)
    run_case("square vs square gap=1", sq_a, sq_b3, False)
    run_case("square vs square thin", sq_a, sq_b4, True)
    run_case("pentagon vs triangle", pent, tri, True)


if __name__ == "__main__":
    main()
