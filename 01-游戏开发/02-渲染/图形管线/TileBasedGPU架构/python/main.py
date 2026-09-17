# -*- coding: utf-8 -*-
"""Tile-Based GPU(TBDR)渲染架构软件模拟。

依据 Arm "How low can you go? Building low-power, low-bandwidth ARM Mali GPUs"
(移动 GPU 内存带宽 4-8 GB/s × 150 pJ/byte ≈ 0.6-1.2 W,带宽吃掉整个功耗预算)、
Arm GPU Best Practices(loadOp/storeOp 语义)与 Jonah Williams 的 TBDR 综述归纳:
  - IMR(立即模式):逐三角形直接读写系统内存中的 framebuffer;
  - TBR/TBDR(图块化):先 binning(三角形进各 tile 的 to-do list),
    再逐 tile 在片上 tile memory 里完成全部光栅化,最后 resolve 写回;
  - MSAA 4x 在 TBDR 上外部带宽近乎"免费"(4 个采样只存在片上,resolve 时平均);
  - loadOp=CLEAR/DONT_CARE 不需要回读,storeOp=DONT_CARE 的附件(如 depth)不写回。
本模拟对同一场景分别跑 IMR 与 TBR 的带宽记账,验证上述结论。
"""
import math

TILE = 16  # 典型 tile 尺寸 16x16(ARM 文章口径 8x8~16x16)


class Triangle:
    def __init__(self, name, bbox, samples_covered):
        """bbox=(x0,y0,x1,y1) 像素包围盒;samples_covered=覆盖的采样点数(每像素 1 或 4)。"""
        self.name = name
        self.bbox = bbox
        self.covered = samples_covered


class Framebuffer:
    """系统内存中的 framebuffer(字节记账用)。"""

    def __init__(self, w, h, bytes_per_sample, msaa):
        self.w, self.h = w, h
        self.bps = bytes_per_sample
        self.msaa = msaa  # 1 或 4

    def attachment_bytes(self):
        return self.w * self.h * self.bps * self.msaa


def make_scene(w, h, n_overdraw):
    """n_overdraw 层全屏三角形(模拟 overdraw)+ 1 层 UI 小三角形。"""
    tris = [Triangle("fullscreen_%d" % i, (0, 0, w, h), w * h) for i in range(n_overdraw)]
    tris.append(Triangle("ui", (0, 0, w // 4, h // 4), (w // 4) * (h // 4)))
    return tris


# ---------- IMR:立即模式渲染器的带宽记账 ----------

def imr_bandwidth(tris, fb, depth_bps=4):
    """逐三角形直接读写系统内存:每采样点 color+depth 各一次读+一次写。"""
    color_rw_bytes = 0
    depth_rw_bytes = 0
    for t in tris:
        n = t.covered * fb.msaa
        color_rw_bytes += n * fb.bps * 2          # 读旧值 + 写新值
        depth_rw_bytes += n * depth_bps * 2       # 深度测试读 + 写
    return color_rw_bytes, depth_rw_bytes


# ---------- TBR:图块化渲染器的带宽记账 ----------

def bin_triangles(tris, w, h, tile):
    """Binning:每个三角形进它包围盒覆盖的所有 tile 的 to-do list。"""
    ntx, nty = math.ceil(w / tile), math.ceil(h / tile)
    todo = {(tx, ty): [] for tx in range(ntx) for ty in range(nty)}
    for t in tris:
        x0, y0, x1, y1 = t.bbox
        # bbox 右/下边界是排他的:覆盖 tile 范围 [x0//tile, ceil(x1/tile))
        for ty in range(int(y0) // tile, min(int(math.ceil(y1 / tile)), nty)):
            for tx in range(int(x0) // tile, min(int(math.ceil(x1 / tile)), ntx)):
                todo[(tx, ty)].append(t)
    return todo


def tbr_bandwidth(tris, fb, depth_bps=4, load_clear=True, store_depth=True):
    """逐 tile:load(tile memory) → 片上光栅化 → resolve → store。

    - binning 阶段:每个三角形按 tile 数写 to-do list(三角形结构小,计一次小开销);
    - 片上阶段:color/depth 读写全在 tile memory,不占外部带宽;
    - 外部带宽只剩:loadOp(LOAD 才有)+ resolve 后的 storeOp(store 才有)。
    """
    w, h = fb.w, fb.h
    todo = bin_triangles(tris, w, h, TILE)
    bin_bytes = sum(len(v) * 64 for v in todo.values())  # to-do list 本身走系统内存
    load_bytes = 0
    store_bytes = 0
    if not load_clear:
        load_bytes += fb.w * fb.h * fb.bps  # loadOp=LOAD:整帧颜色回读
    # storeOp=STORE:颜色必须写回(每像素 1 个 resolve 后样本,MSAA 在片上已平均)
    store_bytes += fb.w * fb.h * fb.bps
    if store_depth:
        # depth 默认在 pass 结束后不再需要 → storeOp=DONT_CARE 可省掉
        store_bytes += fb.w * fb.h * depth_bps
    return bin_bytes, load_bytes, store_bytes


# ---------- 自检 ----------

def main():
    W, H = 256, 256
    COLOR_BPS = 4  # RGBA8
    DEPTH_BPS = 4  # D32 或 D24S8 近似

    # 1) binning 正确性:UI 三角形只进左上角的 4 个 tile
    fb1 = Framebuffer(W, H, COLOR_BPS, 1)
    tris1 = make_scene(W, H, 1)
    todo = bin_triangles(tris1, W, H, TILE)
    assert len(todo) == (W // TILE) * (H // TILE)  # 16x16 = 256 个 tile
    ui_tiles = [k for k, v in todo.items() if any(t.name == "ui" for t in v)]
    # UI 边长 W/4 = 64px = 4×4 个 tile
    assert sorted(ui_tiles) == [(tx, ty) for tx in range(4) for ty in range(4)], ui_tiles
    # UI 区域内的 tile 有 2 个三角形(全屏+UI),区域外只有全屏 1 个
    assert all(len(v) == 2 for k, v in todo.items() if k[0] < 4 and k[1] < 4)
    assert all(len(v) == 1 for k, v in todo.items() if k[0] >= 4 or k[1] >= 4)

    # 2) overdraw 场景:IMR 外部带宽随 overdraw 线性放大,TBR 不变
    for overdraw in (1, 4, 8):
        fb = Framebuffer(W, H, COLOR_BPS, 1)
        tris = make_scene(W, H, overdraw)
        c, d = imr_bandwidth(tris, fb, DEPTH_BPS)
        imr_total = c + d
        b, ld, st = tbr_bandwidth(tris, fb, DEPTH_BPS, load_clear=True, store_depth=False)
        tbr_total = b + ld + st
        assert tbr_total < imr_total, (overdraw, imr_total, tbr_total)
        # IMR 带宽 ∝ overdraw;TBR 与 overdraw 无关(只有 binning 随三角形数微增)
    fb_a = Framebuffer(W, H, COLOR_BPS, 1)
    t1 = sum(tbr_bandwidth(make_scene(W, H, 1), fb_a, DEPTH_BPS, True, False))
    t8 = sum(tbr_bandwidth(make_scene(W, H, 8), fb_a, DEPTH_BPS, True, False))
    assert t8 < t1 * 1.5  # 几乎不变(仅 to-do list 增长)

    # 3) MSAA 4x:IMR 带宽 ×4;TBR 外部带宽几乎不变(采样只在片上)
    fb1x = Framebuffer(W, H, COLOR_BPS, 1)
    fb4x = Framebuffer(W, H, COLOR_BPS, 4)
    tris = make_scene(W, H, 4)
    c1, d1 = imr_bandwidth(tris, fb1x, DEPTH_BPS)
    c4, d4 = imr_bandwidth(tris, fb4x, DEPTH_BPS)
    assert abs(c4 - 4 * c1) < 1 and abs(d4 - 4 * d1) < 1  # 严格 ×4
    b1, l1, s1 = tbr_bandwidth(tris, fb1x, DEPTH_BPS, True, False)
    b4, l4, s4 = tbr_bandwidth(tris, fb4x, DEPTH_BPS, True, False)
    assert s4 == s1 and l4 == l1  # resolve 后仍每像素写 1 个样本 → store 不变
    # 片上 tile memory 需求:4x MSAA 占 4 倍 tile memory(这是 TBDR 的"代价")
    tile_mem_1x = TILE * TILE * (COLOR_BPS + DEPTH_BPS) * 1
    tile_mem_4x = TILE * TILE * (COLOR_BPS + DEPTH_BPS) * 4
    assert tile_mem_4x == 4 * tile_mem_1x

    # 4) loadOp=LOAD 的代价:整帧回读几乎翻倍带宽(反例:应尽量用 CLEAR/DONT_CARE)
    fb2 = Framebuffer(W, H, COLOR_BPS, 1)
    tris2 = make_scene(W, H, 2)
    _, ld_clear, st = tbr_bandwidth(tris2, fb2, DEPTH_BPS, load_clear=True, store_depth=False)
    _, ld_load, _ = tbr_bandwidth(tris2, fb2, DEPTH_BPS, load_clear=False, store_depth=False)
    assert ld_clear == 0
    assert ld_load == W * H * COLOR_BPS
    # loadOp=LOAD 使外部带宽近乎翻倍:load 与 store 字节数恰好相等
    assert ld_load == st and st > 0

    # 5) depth 的 storeOp=DONT_CARE:省掉一整张 depth 附件的写回
    _, _, st_keep = tbr_bandwidth(tris2, fb2, DEPTH_BPS, True, store_depth=True)
    _, _, st_drop = tbr_bandwidth(tris2, fb2, DEPTH_BPS, True, store_depth=False)
    assert st_keep - st_drop == W * H * DEPTH_BPS

    # 6) Arm 文章的功耗口径换算:带宽 4-8 GB/s × 150 pJ/byte ≈ 0.6-1.2 W
    for gbs in (4, 8):
        watts = gbs * 1e9 * 150e-12
        assert 0.5 < watts < 1.3, watts
    print("4-8 GB/s @ 150pJ/byte =", 4 * 1e9 * 150e-12, "to", 8 * 1e9 * 150e-12, "W")

    # 汇总对比
    fb3 = Framebuffer(W, H, COLOR_BPS, 1)
    tris3 = make_scene(W, H, 8)
    c8, d8 = imr_bandwidth(tris3, fb3, DEPTH_BPS)
    tbr8 = sum(tbr_bandwidth(tris3, fb3, DEPTH_BPS, True, False))
    print("ALL TESTS PASSED")
    print("overdraw=8: IMR external BW = %.2f MB, TBR = %.2f MB (%.1fx reduction)"
          % ((c8 + d8) / 1e6, tbr8 / 1e6, (c8 + d8) / tbr8))


if __name__ == "__main__":
    main()
