# -*- coding: utf-8 -*-
"""Forward+ 分块光源剔除(tiled light culling)软件模拟。

依据 3dgep.com "Forward+ Rendering"(O'Donoghue 前向+)与 tiled/clustered
shading 的公开资料归纳:
  - 屏幕划分为 tile 网格,每个 tile 由 4 个过视点的侧平面构成一个"微型视锥";
  - 点光源按球体(视空间球心 C + 半径 r)与 tile 视锥做平面距离测试,
    加上 tile 的 [zMin, zMax] 深度区间测试(代替近/远平面);
  - opaque 列表用 [zMin, zMax],transparent 列表把近端放宽为 0
    (半透明几何可出现在相机与不透明表面之间);
  - clustered shading 再按深度切片(指数分布),解决 2D tile 在深度
    不连续处的"过度指派"。
"""
import math

# ---------- 屏幕与相机 ----------

W, H = 64, 64
TILE = 16
NTX, NTY = W // TILE, H // TILE
FOCAL = W / 2.0  # 针孔相机焦距(像素)


def pixel_ray(px, py):
    """像素中心发出的视空间方向向量(右手系:x 右,y 上,z 前)。"""
    return normalize((
        (px + 0.5 - W / 2.0) / FOCAL,
        (H / 2.0 - py - 0.5) / FOCAL,
        1.0,
    ))


def normalize(v):
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return (v[0] / n, v[1] / n, v[2] / n)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def tile_center_ray(tx, ty):
    return pixel_ray(tx * TILE + TILE / 2.0, ty * TILE + TILE / 2.0)


# ---------- Grid Frustums:每个 tile 的 4 个侧平面(均过视点,d=0) ----------

def build_tile_frustum(tx, ty):
    """四个角点射线两两叉积得平面法线,统一翻转到指向视锥内部。"""
    tl = pixel_ray(tx * TILE, ty * TILE)
    tr = pixel_ray((tx + 1) * TILE, ty * TILE)
    bl = pixel_ray(tx * TILE, (ty + 1) * TILE)
    br = pixel_ray((tx + 1) * TILE, (ty + 1) * TILE)
    center = tile_center_ray(tx, ty)
    planes = [
        cross(tl, bl),   # left
        cross(br, tr),   # right
        cross(tr, tl),   # top
        cross(bl, br),   # bottom
    ]
    out = []
    for n in planes:
        n = normalize(n)
        if dot(n, center) < 0:
            n = (-n[0], -n[1], -n[2])
        out.append(n)
    return out


def sphere_in_frustum(c, r, planes):
    """球-视锥测试:任一平面有向距离 < -r → 整球在平面外侧 → 剔除。"""
    for n in planes:
        if dot(n, c) < -r:
            return False
    return True


# ---------- 场景深度与 tile 深度归约 ----------

class SceneDepth:
    """每像素视空间深度(模拟深度 pre-pass 的输出)。"""

    def __init__(self, base):
        self.px = {}
        for y in range(H):
            for x in range(W):
                self.px[(x, y)] = base

    def patch(self, x0, y0, x1, y1, z):
        for y in range(y0, y1):
            for x in range(x0, x1):
                self.px[(x, y)] = z

    def tile_minmax(self, tx, ty):
        zs = [self.px[(x, y)] for y in range(ty * TILE, (ty + 1) * TILE)
              for x in range(tx * TILE, (tx + 1) * TILE)]
        return min(zs), max(zs)


# ---------- 光源与剔除 ----------

class PointLight:
    def __init__(self, cx, cy, cz, r):
        self.c = (cx, cy, cz)  # 视空间球心
        self.r = r             # 作用半径(Range)


def cull_tile(lights, planes, zmin, zmax, transparent=False):
    """剔除一个 tile:返回光源索引列表(opaque/transparent 两套口径)。"""
    near = 0.0 if transparent else zmin
    out = []
    for i, l in enumerate(lights):
        if not sphere_in_frustum(l.c, l.r, planes):
            continue
        cz = l.c[2]
        if cz - l.r > zmax or cz + l.r < near:
            continue
        out.append(i)
    return out


def build_light_grid(lights, depth):
    grids = {"opaque": {}, "transparent": {}}
    for ty in range(NTY):
        for tx in range(NTX):
            planes = build_tile_frustum(tx, ty)
            zmin, zmax = depth.tile_minmax(tx, ty)
            grids["opaque"][(tx, ty)] = cull_tile(lights, planes, zmin, zmax)
            grids["transparent"][(tx, ty)] = cull_tile(lights, planes, zmin, zmax, True)
    return grids


# ---------- Clustered:tile × 指数深度切片 ----------

def exponential_slices(near, far, n):
    """Z = near * (far/near)^(s/n)(对数均分,抵消 NDC 的非线性)。"""
    return [near * (far / near) ** (s / n) for s in range(n + 1)]


def cluster_of(tx, ty, z, slices):
    for s in range(len(slices) - 1):
        if slices[s] <= z < slices[s + 1]:
            return (tx, ty, s)
    return (tx, ty, len(slices) - 2)  # clamp 到最后一片


def cluster_light_counts(lights, depth, slices):
    """逐像素取其 cluster 的光源数(简化:每个像素独立做簇测试)。"""
    out = {}
    for ty in range(NTY):
        for tx in range(NTX):
            planes = build_tile_frustum(tx, ty)
            for y in range(ty * TILE, (ty + 1) * TILE):
                for x in range(tx * TILE, (tx + 1) * TILE):
                    z = depth.px[(x, y)]
                    cl = cluster_of(tx, ty, z, slices)
                    zs, ze = slices[cl[2]], slices[cl[2] + 1]
                    out[(x, y)] = cull_tile(lights, planes, zs, ze)
    return out


# ---------- 自检 ----------

def main():
    # 1) 视锥平面方向:任一 tile 的 4 个法线都指向 tile 中心射线一侧
    for ty in range(NTY):
        for tx in range(NTX):
            planes = build_tile_frustum(tx, ty)
            c = tile_center_ray(tx, ty)
            for n in planes:
                assert dot(n, c) > 0, (tx, ty, n)

    # 2) 平面剔除的已知用例:tile(0,0) 在屏幕左上,左侧平面 x 分量为负方向
    p00 = build_tile_frustum(0, 0)
    left_n = p00[0]
    assert left_n[0] > 0, left_n  # 左平面的内侧法线指向 +x(视锥内部),TL×BL 的 x 分量为正
    # 球心在 tile 内、半径小 → 通过(tile(0,0) 在 z=1 处覆盖 x∈[-1,-0.5], y∈[0.5,1])
    l_in = PointLight(-0.73, 0.73, 1.0, 0.05)
    assert sphere_in_frustum(l_in.c, l_in.r, p00)
    # 球心在屏幕外左侧、超出半径 → 4 个平面里至少左平面剔除
    l_out = PointLight(-3.0, 0.3, 1.0, 0.05)
    assert not sphere_in_frustum(l_out.c, l_out.r, p00)
    # 球心在屏幕外但半径大 → 与 tile 视锥相交 → 保守保留
    l_near = PointLight(-3.0, 0.3, 1.0, 4.0)
    assert sphere_in_frustum(l_near.c, l_near.r, p00)

    # 3) 深度区间剔除:场景近墙 z=2 + 远墙 z=20,同一 tile 内
    depth = SceneDepth(20.0)   # 默认远处地面
    depth.patch(0, 0, W, H // 2, 2.0)  # 上半屏是近墙 z=2
    zmin, zmax = depth.tile_minmax(0, 0)
    assert (zmin, zmax) == (2.0, 2.0)
    planes = build_tile_frustum(0, 0)
    lights = [
        PointLight(-1.5, 1.5, 2.0, 0.5),    # 0:贴着近墙,在 [2,2] 区间 → 保留
        PointLight(-12.0, 12.0, 19.0, 0.5),  # 1:z 区间 [18.5,19.5] 与 [2,2] 不相交 → 剔除
        PointLight(-12.0, 12.0, 19.0, 18.0),  # 2:半径大到跨回 [zMin,zMax] → 保留
    ]
    lst = cull_tile(lights, planes, zmin, zmax)
    assert lst == [0, 2], lst
    # transparent 口径:近端放宽到 0,但光源 1 的 z 区间 [18.5,19.5] 仍不覆盖 [0,2] → 仍剔除
    lst_t = cull_tile(lights, planes, zmin, zmax, transparent=True)
    assert lst_t == [0, 2], lst_t

    # 4) 全屏光栅:culling 大幅减少逐像素光源遍历
    grid = build_light_grid(lights, depth)
    naive_ops = W * H * len(lights)
    culled = sum(len(grid["opaque"][k]) for k in grid["opaque"])
    avg = culled / (NTX * NTY)
    assert avg < len(lights), (avg, len(lights))

    # 5) clustered 对深度不连续的优势:tile(1,2) 内部左半近墙 z=2、右半远墙 z=20
    depth2 = SceneDepth(20.0)
    depth2.patch(16, 32, 24, 48, 2.0)  # 该 tile 的左半改为近墙
    zmin2, zmax2 = depth2.tile_minmax(1, 2)
    assert (zmin2, zmax2) == (2.0, 20.0)
    # 4 盏贴着远墙的灯:位于 tile(1,2) 的立体角内(z=19.5 处该 tile 覆盖
    # x∈[-9.75,0], y∈[-10.05,-0.31]),只应照亮远墙那半的像素
    far_lights = [PointLight(-6.0 + 1.5 * i, -5.0, 19.5, 0.3) for i in range(4)]
    slices = exponential_slices(0.5, 40.0, 16)
    planes2 = build_tile_frustum(1, 2)
    t_lst = cull_tile(far_lights, planes2, zmin2, zmax2)
    assert len(t_lst) == 4, t_lst  # 2D tile:深度并成 [2,20] → 4 盏全进 tile 列表
    near_t, far_t, near_c, far_c = [], [], [], []
    for y in range(32, 48, 4):
        for x in range(16, 32, 4):
            z = depth2.px[(x, y)]
            cl = cluster_of(1, 2, z, slices)
            c_lst = cull_tile(far_lights, planes2, slices[cl[2]], slices[cl[2] + 1])
            (near_t if z == 2.0 else far_t).append(len(t_lst))
            (near_c if z == 2.0 else far_c).append(len(c_lst))
    # 纯 2D tile:近墙像素也背上远墙那 4 盏灯;clustered 则为 0(近墙切片
    # 的 z 区间约 [1.96,2.58],与远灯 z 区间 [19.2,19.8] 不相交)
    assert all(c == 4 for c in near_t), near_t
    assert all(c == 0 for c in near_c), near_c
    assert all(c == 4 for c in far_c), far_c

    print("ALL TESTS PASSED")
    print("tile grid: %dx%d tiles of %dpx" % (NTX, NTY, TILE))
    print("opaque grid avg lights/tile = %.2f (vs %d total)" % (avg, len(lights)))
    print("depth-discontinuity tile: tiled near-pixel = %d lights, clustered = %d" % (near_t[0], near_c[0]))


if __name__ == "__main__":
    main()
