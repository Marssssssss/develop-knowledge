#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Z-Buffer(深度缓冲)与 Z-Fighting 的最小软件光栅化演示。

场景 A:两个互相贯穿的三角形。画家算法按平均深度排序绘制,贯穿处必然
        出错(平均深度相同的两个三角形,后画者整体获胜);Z-Buffer 逐像素
        深度测试得到正确的相交线。
场景 B:一对近平行三角形,D 恒比 C 近 0.3 个世界单位。浮点深度与
        24-bit 定点深度下 D 全胜;16-bit 深度下远处量化步长超过 0.3,
        出现经典的 Z-Fighting 条带。

深度模型依据 Khronos OpenGL Wiki《Depth Buffer Precision》:
透视投影下深度缓冲实际存储 1/z 的线性映射,定点量化精度在远端急剧恶化。
"""

import math

W, H = 64, 24            # ASCII 帧缓冲尺寸(列 x 行)
NEAR, FAR = 1.0, 400.0   # 近/远裁剪面:近面离 0 越近,整体精度越差
FOCAL = 40.0             # 针孔投影焦距(像素单位)


def project(p):
    """透视投影:相机空间(右手系,z 朝前为正)-> 像素坐标(含透视除法)。"""
    x, y, z = p
    return (W / 2.0 + FOCAL * x / z, H / 2.0 - FOCAL * y / z)


class Canvas:
    """帧缓冲 + 深度缓冲。bits=0 表示浮点深度,否则为定点量化位数。"""

    def __init__(self, bits):
        self.color = [['.'] * W for _ in range(H)]
        self.depth = [[math.inf] * W for _ in range(H)]
        self.bits = bits

    def to_buffer(self, z):
        """把相机空间 z 编码为缓冲值。

        归一化到 [0,1]:z=NEAR -> 0(最近),z=FAR -> 1(最远)。
        注意对 1/z 线性——这正是透视除法后屏幕空间的表现。
        """
        d = (1.0 / z - 1.0 / NEAR) / (1.0 / FAR - 1.0 / NEAR)
        if self.bits:
            return round(d * ((1 << self.bits) - 1))
        return d

    def put(self, x, y, z, tag):
        """深度测试(GL_LESS:严格小于才写入)+ 帧缓冲写入。"""
        d = self.to_buffer(z)
        if d < self.depth[y][x]:
            self.depth[y][x] = d
            self.color[y][x] = tag
            return True
        return False

    def blit(self, x, y, tag):
        """无深度测试,直接覆盖(画家算法)。"""
        self.color[y][x] = tag

    def show(self, title):
        border = '+' + '-' * W + '+'
        print('\n' + title)
        print(border)
        for row in self.color:
            print('|' + ''.join(row) + '|')
        print(border)


def rasterize(cv, tri, tag, depth_test=True):
    """三角形光栅化:包围盒遍历 + 重心坐标,逐片元处理。

    透视校正:投影除法后 1/z 在屏幕空间是线性的,因此对顶点 1/z 做
    重心插值再取倒数,即得该片元正确的相机空间深度(与 GPU 行为一致)。
    """
    a, b, c = (project(p) for p in tri)
    x0 = max(int(min(a[0], b[0], c[0])), 0)
    x1 = min(int(max(a[0], b[0], c[0])) + 1, W - 1)
    y0 = max(int(min(a[1], b[1], c[1])), 0)
    y1 = min(int(max(a[1], b[1], c[1])) + 1, H - 1)
    area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if abs(area) < 1e-12:
        return
    inv = (1.0 / tri[0][2], 1.0 / tri[1][2], 1.0 / tri[2][2])
    for py in range(y0, y1 + 1):
        for px in range(x0, x1 + 1):
            # e0/e1/e2 分别是顶点 0/1/2 的(未归一化)重心权重
            e0 = (c[0] - b[0]) * (py - b[1]) - (c[1] - b[1]) * (px - b[0])
            e1 = (a[0] - c[0]) * (py - c[1]) - (a[1] - c[1]) * (px - c[0])
            e2 = (b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0])
            neg = area < 0
            if (e0 < 0) != neg or (e1 < 0) != neg or (e2 < 0) != neg:
                continue  # 片元中心在三角形外(符号不一致)
            if depth_test:
                iz = (e0 * inv[0] + e1 * inv[1] + e2 * inv[2]) / area
                cv.put(px, py, 1.0 / iz, tag)
            else:
                cv.blit(px, py, tag)


def precision_table():
    """打印不同距离处的深度分辨率(依据 Khronos Wiki 推导)。"""
    print('\n深度分辨率表(相邻两个可表示深度的世界空间距离 dz,越小越好):')
    print('  依据 Khronos OpenGL Wiki: dz ~= z^2*(FAR-NEAR)/(FAR*NEAR*(2^bits-1))')
    print('  NEAR=%.0f, FAR=%.0f' % (NEAR, FAR))
    print('  %6s %12s %12s' % ('z', '16-bit dz', '24-bit dz'))
    for z in (10, 50, 100, 200, 300, 400):
        base = z * z * (FAR - NEAR) / (FAR * NEAR)
        print('  %6.0f %12.4f %12.6f' % (z, base / 65535.0, base / 16777215.0))
    print('  经验法则:log2(FAR/NEAR) = %.1f bit 精度损失(OpenGL 蓝皮书)'
          % math.log2(FAR / NEAR))


# 场景 A:互相贯穿的三角形对(平均深度相同——画家算法的噩梦)
# T1 平面方程 z = 3.2 + 0.5*y,T2 镜像 z = 3.2 - 0.5*y,二者在 y=0 处相交
SCENE_A = [
    ((-1.8, -0.7, 2.85), (1.8, -0.7, 2.85), (0.0, 0.7, 3.55)),  # 'A'
    ((-1.8, 0.7, 2.85), (1.8, 0.7, 2.85), (0.0, -0.7, 3.55)),   # 'B'
]

# 场景 B:近平行三角形对,D 比 C 全场恒定近 0.3 个世界单位
SLANT_C = ((-100.0, -40.0, 100.0), (100.0, -40.0, 300.0), (0.0, 40.0, 200.0))
SLANT_D = tuple((x, y, z + 0.3) for x, y, z in SLANT_C)


def main():
    print('=' * 66)
    print('场景 A:互相贯穿的三角形 —— 画家算法 vs Z-Buffer')
    print('=' * 66)

    painter = Canvas(0)
    # 从远到近排序绘制;两三角形平均深度相同,排序稳定 -> 先 A 后 B
    order = sorted(SCENE_A, key=lambda t: -sum(p[2] for p in t) / 3.0)
    for tri, tag in zip(order, 'AB'):
        rasterize(painter, tri, tag, depth_test=False)
    painter.show('画家算法(无深度测试,后画的 B 整体覆盖,贯穿处错误):')

    zbuf = Canvas(0)
    for tri, tag in zip(SCENE_A, 'AB'):
        rasterize(zbuf, tri, tag, depth_test=True)
    zbuf.show('Z-Buffer 浮点深度(逐像素测试,相交线正确):')

    print()
    print('=' * 66)
    print('场景 B:近平行三角形对(D 恒比 C 近 0.3)—— 深度位数与 Z-Fighting')
    print('=' * 66)
    modes = (
        (0, '浮点深度(正确:D 全胜)'),
        (16, '16-bit 定点深度(远处量化步长 > 0.3,Z-Fighting 条带)'),
        (24, '24-bit 定点深度(步长足够小,D 仍全胜)'),
    )
    for bits, name in modes:
        cv = Canvas(bits)
        rasterize(cv, SLANT_C, 'C', depth_test=True)
        rasterize(cv, SLANT_D, 'D', depth_test=True)
        cv.show('Z-Buffer %s:' % name)

    precision_table()


if __name__ == '__main__':
    main()
