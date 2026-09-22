"""hrtf.py 自检：坐标换算 / 最近邻 / 反距离插值 / 延迟单位 / ITD-ILD。实跑版。"""

import math

from hrtf import (HEAD_RADIUS, HRTF, c2s, distance, fequals, getfilter,
                  ild_db, interpolate, itd_seconds, nearest_index,
                  render_mono, s2c)

TOL = 1e-9
_ok = 0
_bad = []


def check(name, got, want, tol=TOL):
    global _ok
    if isinstance(want, float) or isinstance(got, float):
        good = abs(got - want) <= tol
    else:
        good = got == want
    if good:
        _ok += 1
    else:
        _bad.append("%s: got %r want %r" % (name, got, want))


def near(name, got, want, tol=1e-6):
    check(name, got, want, tol)


def vec_near(name, got, want, tol=1e-9):
    check(name, len(got) == len(want), True)
    for i, (g, w) in enumerate(zip(got, want)):
        near("%s[%d]" % (name, i), g, w, tol)


def raises(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    return False


# ------------------------------------------------------------ 坐标
vec_near("s2c 正前", s2c([0, 0, 1]), [1, 0, 0])
vec_near("s2c 正左", s2c([90, 0, 1]), [0, 1, 0])
vec_near("s2c 正上", s2c([0, 90, 1]), [0, 0, 1])
vec_near("s2c 正右", s2c([-90, 0, 1]), [0, -1, 0])
vec_near("s2c 正后", s2c([180, 0, 1]), [-1, 0, 0])
near("c2s 方位角规整到 270", c2s([0, -1, 0])[0], 270.0)
near("c2s 正上仰角 90", c2s([0, 0, 1])[1], 90.0)
vec_near("往返 37/-12/1.7", c2s(s2c([37, -12, 1.7])), [37, -12, 1.7], 1e-9)
vec_near("往返 200/30/2.5", c2s(s2c([200, 30, 2.5])), [200, 30, 2.5], 1e-9)
near("fequals 1e-6 内相等", fequals(1.0, 1.000001), True)
near("fequals 1e-4 外不等", fequals(1.0, 1.0001), False)
near("distance 3-4-5", distance([0, 0, 0], [3, 4, 0]), 5.0)
near("头半径默认 0.09", HEAD_RADIUS, 0.09)

# ------------------------------------------------------------ 合成 HRTF
POS = [[0, 0, 1], [90, 0, 1], [180, 0, 1], [270, 0, 1]]  # 前 / 左 / 后 / 右
IRS = [
    [[1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]],      # 前：两耳等响
    [[0.8, 0.2, 0.0, 0.0], [0.4, 0.1, 0.0, 0.0]],      # 左：左耳更响
    [[0.6, 0.0, 0.0, 0.0], [0.6, 0.0, 0.0, 0.0]],      # 后
    [[0.4, 0.1, 0.0, 0.0], [0.8, 0.2, 0.0, 0.0]],      # 右
]
DELAYS = [[0.0, 0.0], [0.0, 0.0003], [0.0, 0.0], [0.0003, 0.0]]

hrtf = HRTF(POS, IRS, 48000.0, DELAYS)
check("M", hrtf.M, 4)
check("R", hrtf.R, 2)
check("N", hrtf.N, 4)
vec_near("SourcePosition 转笛卡尔", hrtf.source_cartesian[1], [0.0, 1.0, 0.0])
check("validate 通过", hrtf.validate(), True)
broken = HRTF(POS, IRS, 48000.0, DELAYS)
broken.source_elements = 11
check("元数据不一致 -> INVALID_FORMAT", raises(getfilter, broken, [1, 0, 0]), True)

# ------------------------------------------------------------ 最近邻与命中
check("最近点 (0.9,0.1,0) -> 前", nearest_index(hrtf, [0.9, 0.1, 0.0]), 0)
check("最近点 (0.1,0.9,0) -> 左", nearest_index(hrtf, [0.1, 0.9, 0.0]), 1)
check("最近点 (-1.0,0.05,0) -> 后", nearest_index(hrtf, [-1.0, 0.05, 0.0]), 2)

l, r, dl, dr = getfilter(hrtf, [1.0, 0.0, 0.0])
vec_near("命中直取左耳", l, [1.0, 0.0, 0.0, 0.0])
vec_near("命中直取右耳", r, [1.0, 0.0, 0.0, 0.0])
near("命中延迟（秒）", dl, 0.0)
near("命中延迟（秒）右", dr, 0.0)
l2, r2, dl2, dr2 = getfilter(hrtf, [0.0, 1.0, 0.0])
vec_near("左侧命中左耳 IR", l2, [0.8, 0.2, 0.0, 0.0])
near("左侧延迟右耳更长（秒）", dr2, 0.0003)

# 短整型接口：延迟 = 秒 * 采样率，向零截断
l3, r3, dls, drs = getfilter(hrtf, [0.0, 1.0, 0.0], None, True, "short")
check("延迟样本数 = int(0.0003*48000)", drs, 14)
check("左耳 0 样本", dls, 0)

# ------------------------------------------------------------ 插值
q = [0.5, 0.5, 0.0]  # 前与左的中点
d_front = math.sqrt(0.5 ** 2 + 0.5 ** 2)      # 到 (1,0,0)
d_back = math.sqrt(1.5 ** 2 + 0.5 ** 2)       # 到 (-1,0,0)

# 1) 邻域全空：权重归一化后退化成最近点
l4, r4, _, _ = interpolate(hrtf, q, 0, [-1] * 6)
vec_near("邻域全空 -> 退化最近点", l4, [1.0, 0.0, 0.0, 0.0])

# 2) 成对且等距：两者都不参与（tools.h 的 !fequals 分支）
l5, r5, _, _ = interpolate(hrtf, q, 0, [0, 1, -1, -1, -1, -1])
vec_near("等距对 -> 谁都不用", l5, [1.0, 0.0, 0.0, 0.0])

# 3) 槽 0 有效（后），槽 1 空缺 -> 参与并按 1/d 加权
l6, r6, _, _ = interpolate(hrtf, q, 0, [2, -1, -1, -1, -1, -1])
w0, w2 = 1.0 / d_front, 1.0 / d_back
tot = w0 + w2
near("加权插值 tap0", l6[0], (1.0 * w0 + 0.6 * w2) / tot, 1e-9)
near("加权插值 tap1", l6[1], (0.0 * w0 + 0.0 * w2) / tot, 1e-9)
near("加权插值权重和为 1", w0 / tot + w2 / tot, 1.0)

# 4) 一对中取更近者：槽位 (0=后, 1=左) 中左更近
l7, _, _, _ = interpolate(hrtf, q, 0, [2, 1, -1, -1, -1, -1])
d_left = math.sqrt(0.5 ** 2 + 0.5 ** 2)
tot2 = 1.0 / d_front + 1.0 / d_left
near("取更近邻 tap0", l7[0], (1.0 / d_front + 0.8 / d_left) / tot2, 1e-9)
near("取更近邻 tap1", l7[1], (0.0 / d_front + 0.2 / d_left) / tot2, 1e-9)

# 5) 绕过插值：用最近点坐标覆盖请求坐标
ln, _, _, _ = getfilter(hrtf, [0.6, 0.45, 0.0], None, False, "float")
vec_near("nointerp 得到存储值", ln, [1.0, 0.0, 0.0, 0.0])
li, _, _, _ = getfilter(hrtf, [0.6, 0.45, 0.0], [2, -1, -1, -1, -1, -1], True, "float")
near("带邻域插值后不再是存储值", li[0] < 1.0, True)

# ------------------------------------------------------------ 延迟维度
shared = HRTF(POS, IRS, 48000.0, [[0.0, 0.0], [0.0, 0.0]])
check("共享 DataDelay（elements == R）", shared.delay_pair(3), [0.0, 0.0])
shared.delays = [[0.001, 0.002], [0.0, 0.0]]
check("共享时恒取 [0]/[1]", shared.delay_pair(3), [0.001, 0.002])
check("每测量点 DataDelay", hrtf.delay_pair(3), [0.0003, 0.0])

# ------------------------------------------------------------ ITD / ILD
# 0.3ms 在 48kHz 下是 14.4 样本，短整型接口向零截断成 14 -> 291.7us
near("左侧源 ITD = 14/48000", itd_seconds(0, 14, 48000.0), 14.0 / 48000.0)
near("右侧源 ITD 为负", itd_seconds(14, 0, 48000.0), -14.0 / 48000.0)
near("截断损失 = 0.3ms - 291.7us", 0.0003 - 14.0 / 48000.0, 8.333333333e-06, 1e-12)
near("左侧源 ILD = -6.02dB", ild_db(IRS[1][0], IRS[1][1]), -6.020599913279624, 1e-9)
near("右侧源 ILD = +6.02dB", ild_db(IRS[3][0], IRS[3][1]), 6.020599913279624, 1e-9)
near("等响 ILD = 0dB", ild_db(IRS[0][0], IRS[0][1]), 0.0)
near("对称源 ILD 反号",
     ild_db(IRS[1][0], IRS[1][1]) + ild_db(IRS[3][0], IRS[3][1]), 0.0, 1e-9)

# ------------------------------------------------------------ 渲染闭环
out_l, out_r = render_mono(hrtf, [1.0, 0.0, 0.0], [1.0])
check("卷积输出长度 = 输入+N-1+延迟", len(out_l), 1 + 4 - 1 + 0)
near("冲激响应首拍", out_l[0], 1.0)
near("无延迟声道对齐", out_r[0], 1.0)
out_l2, out_r2 = render_mono(hrtf, [0.0, 1.0, 0.0], [1.0])
near("左耳先到", out_l2[0], 0.8)
near("右耳延迟 14 样本", out_r2[14], 0.4)
near("右耳 0 样本处为 0", out_r2[0], 0.0)

print("assertions ok: %d, failed: %d" % (_ok, len(_bad)))
for line in _bad:
    print("FAILED", line)
if _bad:
    raise SystemExit(1)
print("ALL GREEN")
