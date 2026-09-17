# -*- coding: utf-8 -*-
"""延迟渲染 G-Buffer:几何 pass 写入 G-buffer、光照 pass 重建片元数据。

依据 LearnOpenGL "Deferred Shading" 归纳的原理做软件模拟:
  - Geometry Pass(MRT):把世界空间位置 / 法线 / 反照率+镜面写入 G-buffer,
    深度测试保证每像素只保留最上层片元。
  - Lighting Pass:屏幕四边形逐像素从 G-buffer 采样重建片元数据计算光照。
  - Light Volume:由衰减方程与 5/256 阈值反解光源作用半径,渲染背面球体,
    把光照计算量从 O(pixels*lights) 降到 O(pixels + lights)。
"""
import math

# ---------- 场景定义 ----------

W, H = 16, 16  # 模拟用极小分辨率,算法与真实管线一致


class Quad:
    """屏幕对齐的简单四边形:覆盖像素矩形 [x0,x1)*[y0,y1),带法线与材质。"""

    def __init__(self, name, x0, y0, x1, y1, depth, normal, albedo, spec):
        self.name = name
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.depth = depth          # 深度越小越靠近相机
        self.normal = normal        # (nx, ny, nz) 单位法线
        self.albedo = albedo        # (r, g, b)
        self.spec = spec            # 镜面强度,打包进 G-buffer 的 alpha


def make_scene():
    """两层重叠四边形:前景 depth 小、背景 depth 大 → 深度测试只留前景。"""
    return [
        # 背景:覆盖全屏,法线朝 +z
        Quad("background", 0, 0, W, H, 0.9, (0.0, 0.0, 1.0), (0.4, 0.4, 0.4), 0.1),
        # 前景:只覆盖左半屏,深度更小 → 胜出
        Quad("foreground", 0, 0, W // 2, H, 0.5, (0.0, 0.0, 1.0), (0.8, 0.2, 0.2), 0.5),
    ]


class PointLight:
    def __init__(self, pos, color, kc=1.0, kl=0.09, kq=0.032):
        self.pos = pos        # 世界空间位置
        self.color = color    # (r,g,b)
        self.kc, self.kl, self.kq = kc, kl, kq


def light_volume_radius(light):
    """按 LearnOpenGL:取最亮颜色分量,解 Kq*d^2 + Kl*d + Kc - Imax*256/5 = 0。"""
    imax = max(light.color)
    b = light.kl
    a = light.kq
    c = light.kc - (256.0 / 5.0) * imax
    disc = b * b - 4.0 * a * c
    if disc < 0:
        return 0.0
    return (-b + math.sqrt(disc)) / (2.0 * a)


# ---------- Geometry Pass:光栅化 + 深度测试,写入 G-buffer ----------

class GBuffer:
    """模拟 3 张 G-buffer 纹理(RGBA16F 位置 / RGBA16F 法线 / RGBA8 反照率+镜面)。"""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.position = {}   # (x,y) -> (px,py,pz)
        self.normal = {}
        self.albedo_spec = {}
        self.depth = {}

    def gbuffer_bits_per_pixel(self):
        # RGBA16F=64b ×2 + RGBA8=32b + depth24(LearnOpenGL 教程配置的量化口径)
        return 64 + 64 + 32 + 24

    def megabytes_at(self, w, h):
        return self.gbuffer_bits_per_pixel() * w * h / 8 / 1024 / 1024


def geometry_pass(quads, gb):
    for q in quads:
        for y in range(q.y0, q.y1):
            for x in range(q.x0, q.x1):
                # 深度测试:更小的 depth 胜出(模拟 early-z,G-buffer 只留最上层片元)
                if (x, y) not in gb.depth or q.depth < gb.depth[(x, y)]:
                    gb.depth[(x, y)] = q.depth
                    # 位置由深度反推 z,场景摆在 z 轴正前方(相机在原点看 +z)
                    gb.position[(x, y)] = (float(x), float(y), q.depth)
                    gb.normal[(x, y)] = q.normal
                    gb.albedo_spec[(x, y)] = (q.albedo[0], q.albedo[1], q.albedo[2], q.spec)
    return gb


# ---------- Lighting Pass:逐像素从 G-buffer 重建数据 ----------

def attenuation(d, light):
    return 1.0 / (light.kc + light.kl * d + light.kq * d * d)


def shade_pixel(pix_xy, gb, lights):
    """标准 Blinn-Phong 漫反射光照:输入全部来自 G-buffer(与 forward 相同的数学)。"""
    frag_pos = gb.position[pix_xy]
    nx, ny, nz = gb.normal[pix_xy]
    albedo = gb.albedo_spec[pix_xy][:3]
    out = [a * 0.1 for a in albedo]  # 环境光
    for light in lights:
        lx, ly, lz = light.pos
        dx, dy, dz = lx - frag_pos[0], ly - frag_pos[1], lz - frag_pos[2]
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        diff = max(0.0, (nx * dx + ny * dy + nz * dz) / dist if dist > 0 else 0.0)
        att = attenuation(dist, light)
        out[0] += diff * att * albedo[0] * light.color[0]
        out[1] += diff * att * albedo[1] * light.color[1]
        out[2] += diff * att * albedo[2] * light.color[2]
    return tuple(out)


def forward_render(quads, lights):
    """对照实现:逐物体逐像素直接计算光照(不写 G-buffer)。"""
    depth = {}
    color = {}
    for q in quads:
        for y in range(q.y0, q.y1):
            for x in range(q.x0, q.x1):
                if (x, y) not in depth or q.depth < depth[(x, y)]:
                    depth[(x, y)] = q.depth
                    color[(x, y)] = q
    out = {}
    for (x, y), q in color.items():
        frag_pos = (float(x), float(y), q.depth)
        nx, ny, nz = q.normal
        c = [a * 0.1 for a in q.albedo]
        for light in lights:
            lx, ly, lz = light.pos
            dx, dy, dz = lx - frag_pos[0], ly - frag_pos[1], lz - frag_pos[2]
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            diff = max(0.0, (nx * dx + ny * dy + nz * dz) / dist if dist > 0 else 0.0)
            att = attenuation(dist, light)
            for k in range(3):
                c[k] += diff * att * q.albedo[k] * light.color[k]
        out[(x, y)] = tuple(c)
    return out


# ---------- Light Volume:只照亮球体投影覆盖的像素 ----------

def light_volume_pixels(light, gb):
    """半径 = light_volume_radius(light);只遍历球心像素邻域内的像素。"""
    r = light_volume_radius(light)
    lx, ly, _ = light.pos
    out = []
    for (x, y), frag in gb.position.items():
        # 像素到球心的世界空间距离(只用 x/y/z 三轴)
        d = math.sqrt((x - lx) ** 2 + (y - ly) ** 2 + (frag[2] - light.pos[2]) ** 2)
        if d < r:
            out.append((x, y))
    return out, r


def deferred_with_volumes(gb, lights):
    """光照 pass + light volume:每光源只在其体积覆盖的像素上累计光照。"""
    out = {}
    base = {}
    for pix in gb.position:
        albedo = gb.albedo_spec[pix][:3]
        base[pix] = [a * 0.1 for a in albedo]
        out[pix] = [a * 0.1 for a in albedo]
    ops = 0
    for light in lights:
        for pix in light_volume_pixels(light, gb)[0]:
            frag_pos = gb.position[pix]
            nx, ny, nz = gb.normal[pix]
            dx, dy, dz = light.pos[0] - frag_pos[0], light.pos[1] - frag_pos[1], light.pos[2] - frag_pos[2]
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            diff = max(0.0, (nx * dx + ny * dy + nz * dz) / dist if dist > 0 else 0.0)
            att = attenuation(dist, light)
            albedo = gb.albedo_spec[pix][:3]
            for k in range(3):
                out[pix][k] += diff * att * albedo[k] * light.color[k]
            ops += 1
    return {p: tuple(c) for p, c in out.items()}, ops


# ---------- 自检 ----------

def approx(a, b, tol=1e-9):
    return all(abs(x - y) < tol for x, y in zip(a, b)) and len(a) == len(b)


def main():
    quads = make_scene()
    gb = GBuffer(W, H)
    geometry_pass(quads, gb)

    # 1) 深度测试:前景(depth 0.5)覆盖左半屏,背景只出现在右半屏
    assert gb.depth[(1, 1)] == 0.5 and gb.depth[(W - 1, H - 1)] == 0.9
    assert gb.albedo_spec[(1, 1)] == (0.8, 0.2, 0.2, 0.5)
    assert gb.albedo_spec[(W - 1, 1)][0] == 0.4  # 右半屏是背景
    assert len(gb.depth) == W * H  # 每像素恰好一个最上层片元

    # 2) G-buffer 内存口径:1080p 下该配置约多少 MB(64+64+32+24 bit/px)
    mb = gb.megabytes_at(1920, 1080)
    assert 44 < mb < 47, mb  # ≈45.5 MiB(教程口径:单张 RGBA32bpp 纹理就要 8.29MB)

    # 3) light volume 半径:5/256 阈值反解,衰减系数越大半径越小
    l1 = PointLight((8, 8, 0.2), (1.0, 1.0, 1.0), 1.0, 0.09, 0.032)
    l2 = PointLight((8, 8, 0.2), (1.0, 1.0, 1.0), 1.0, 0.7, 0.18)
    r1, r2 = light_volume_radius(l1), light_volume_radius(l2)
    assert r1 > r2 > 0, (r1, r2)
    # 验证解确实满足阈值方程:attenuation(r1)*Imax ≈ 5/256
    assert abs(attenuation(r1, l1) - 5.0 / 256.0) < 1e-9, attenuation(r1, l1)

    # 4) 正确性:deferred 与 forward 对同一场景输出完全一致(数学解耦、结果相同)
    lights = [
        PointLight((4, 4, 0.0), (1.0, 1.0, 1.0)),
        PointLight((12, 8, 0.0), (0.5, 0.5, 0.5)),
        PointLight((8, 14, 0.0), (0.8, 0.4, 0.2)),
    ]
    fwd = forward_render(quads, lights)
    for pix in gb.position:
        d = shade_pixel(pix, gb, lights)
        assert approx(d, fwd[pix]), (pix, d, fwd[pix])

    # 5) 光照计算量:多光源时 light volume 让 deferred 反超 forward
    #    (kl/kq 取大 → 每盏灯体积半径约 5px,只覆盖屏幕一小部分)
    many = [PointLight((x % W, y % H, 0.0), (0.5, 0.5, 0.5), 1.0, 0.7, 0.7)
            for x in range(0, W, 2) for y in range(0, H, 4)]
    fwd_ops = W * H * len(many)                       # forward:每像素遍历全部光源
    _, def_ops = deferred_with_volumes(gb, many)     # deferred:只有体积覆盖处才算
    assert def_ops < fwd_ops, (def_ops, fwd_ops)

    # 6) 少光源时相反:体积覆盖了几乎全屏,deferred 的 G-buffer 写入是纯开销
    few = [PointLight((8, 8, 0.0), (3.0, 3.0, 3.0))]  # 很亮的单光源 → 大体积
    fwd_ops_few = W * H * len(few)
    out_few, def_ops_few = deferred_with_volumes(gb, few)
    deferred_total_few = def_ops_few + W * H  # 加上 geometry pass 的逐像素写入
    assert deferred_total_few > fwd_ops_few, (deferred_total_few, fwd_ops_few)
    # 而且单亮光源下两者结果仍一致
    fwd_few = forward_render(quads, few)
    for pix in gb.position:
        assert approx(out_few[pix], fwd_few[pix])

    print("ALL TESTS PASSED")
    print("gbuffer bits/px =", gb.gbuffer_bits_per_pixel(), "; 1080p =", round(mb, 1), "MiB")
    print("lights=%d: forward ops=%d, deferred(volume) ops=%d" % (len(many), fwd_ops, def_ops))
    print("lights=1: forward ops=%d, deferred total ops=%d (deferred 更贵)" % (fwd_ops_few, deferred_total_few))


if __name__ == "__main__":
    main()
