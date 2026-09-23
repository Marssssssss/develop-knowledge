"""USL 自检：与 R 包 usl 的已发表系数/统计量逐项对照。"""

import math
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from uslmodel import (  # noqa: E402
    RAYTRACER,
    SPECDM91,
    USLModel,
    fit,
    jacobian,
    residual_std_error,
    sse,
    usl_C,
    usl_X,
)

FAILED = []
N = 0


def ck(cond, msg):
    global N
    N += 1
    if not cond:
        FAILED.append(msg)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def rel(a, b):
    """相对误差。"""
    return abs(a - b) / max(1e-30, abs(b))


# ---------------------------------------------------- 1. 解析 Jacobian 对拍
for (n_i, g, a, b) in ((4.0, 20.0, 0.05, 0.0002), (64.0, 90.0, 0.02, 0.0001),
                       (1.0, 5.0, 0.3, 0.01), (216.0, 89.9, 0.0277, 0.0001)):
    # 中心差分步长要照顾 β 这一项（N²(N−1) 放大了截断误差），1e-7 实测三项都 <1e-7
    h = 1e-7
    num = []
    for k, p in enumerate((g, a, b)):
        up = list((g, a, b))
        dn = list((g, a, b))
        up[k] = p + h
        dn[k] = p - h
        num.append((usl_X(n_i, *up) - usl_X(n_i, *dn)) / (2 * h))
    ana = jacobian(n_i, g, a, b)
    for i in range(3):
        ck(rel(num[i], ana[i]) < 1e-6, f"Jacobian[{i}] 解析 == 数值（N={n_i}）")

# ------------------------------------------------------------- 2. 模型基本式
# C(N) = X(N)/X(1)，且 γ 只缩放 y 轴
ck(close(usl_X(4, 20.0, 0.05, 0.0002) / usl_X(1, 20.0, 0.05, 0.0002),
         usl_C(4, 0.05, 0.0002)), "C(N) = X(N)/X(1)")
ck(close(usl_C(1, 0.05, 0.0002), 1.0), "C(1) = 1")
ck(close(usl_X(10, 7.0, 0.05, 0.0002) / usl_X(10, 1.0, 0.05, 0.0002), 7.0),
   "γ 只缩放 y 轴")
# α=β=0 时退化为直线 γN
for n_i in (1, 2, 10, 100):
    ck(close(usl_X(n_i, 3.0, 0.0, 0.0), 3.0 * n_i), f"α=β=0 时 X({n_i})=γN")
# β=0, γ=1 时 C(N) = N/(1+α(N-1))，即 Amdahl 形状
for n_i in (1, 2, 8, 64):
    ck(close(usl_C(n_i, 0.1, 0.0), n_i / (1 + 0.1 * (n_i - 1))),
       f"β=0 时 C({n_i}) = N/(1+α(N-1))")

# --------------------------------------------------- 3. raytracer：拟合对照
m_ray, sse_ray = fit(RAYTRACER)
PUB_RAY = (21.848843, 0.057771, 0.000000)
ck(rel(m_ray.gamma, PUB_RAY[0]) < 1e-5, f"ray γ 拟合 {m_ray.gamma} ≈ {PUB_RAY[0]}")
ck(rel(m_ray.alpha, PUB_RAY[1]) < 1e-4, f"ray α 拟合 {m_ray.alpha} ≈ {PUB_RAY[1]}")
ck(m_ray.beta < 1e-9, f"ray β 拟合撞 0 边界（{m_ray.beta}）")
# 已发表系数处的 SSE 不比我们的拟合差（说明它确实是极小点）
sse_pub = sse(RAYTRACER, *PUB_RAY)
ck(sse_pub <= sse_ray * 1.000001 or abs(sse_pub - sse_ray) < 1e-6,
   f"ray 已发表系数是极小点（{sse_pub:.4f} vs {sse_ray:.4f}）")
ck(close(sse_pub, 697.2378, tol=1e-4), "ray SSE = 697.2378")
ck(abs(residual_std_error(RAYTRACER, *PUB_RAY) - 9.34) < 0.02, "ray RSE ≈ 9.34")
ck(len(RAYTRACER) - 3 == 8, "ray 自由度 = 8")

ray_pub = USLModel(*PUB_RAY)
ck(not ray_pub.has_peak, "β=0 -> 无有限峰值")
ck(math.isinf(ray_pub.peak()[0]), "β=0 时 Nmax 在无穷远")
ck(abs(ray_pub.limit() - 378) < 1.0, f"ray Amdahl 渐近线 {ray_pub.limit():.1f} ≈ 378")
nopt, xopt = ray_pub.optimal()
ck(abs(nopt - 17.3) < 0.05, f"ray Nopt {nopt:.2f} ≈ 17.3")
ck(abs(xopt - 195) < 2.0, f"ray Xopt {xopt:.1f} ≈ 195")
# 渐近线性质：C(N) 随 N 单调增但收敛到 1/α
ck(ray_pub.C(1000) < ray_pub.C(10000) < 1.0 / PUB_RAY[1], "ray 曲线单调逼近 1/α")
ck(close(ray_pub.C(10 ** 7), 1.0 / PUB_RAY[1], tol=1e-4), "ray 极限 = 1/α")

# --------------------------------------------------- 4. specsdm91：拟合对照
m_spec, sse_spec = fit(SPECDM91)
PUB_SPEC = (89.9952382, 0.0277285, 0.0001044)
ck(rel(m_spec.gamma, PUB_SPEC[0]) < 1e-5, f"spec γ 拟合 {m_spec.gamma}")
ck(rel(m_spec.alpha, PUB_SPEC[1]) < 1e-4, f"spec α 拟合 {m_spec.alpha}")
ck(rel(m_spec.beta, PUB_SPEC[2]) < 1e-3, f"spec β 拟合 {m_spec.beta}")
sse_pub_s = sse(SPECDM91, *PUB_SPEC)
# 两边都是极小点，差值只来自收敛容差：相对差必须 < 1e-5
ck(rel(sse_pub_s, sse_spec) < 1e-5,
   f"spec 已发表系数是极小点（{sse_pub_s:.3f} vs {sse_spec:.3f}）")
ck(abs(residual_std_error(SPECDM91, *PUB_SPEC) - 82.8) < 0.2, "spec RSE ≈ 82.8")
ck(len(SPECDM91) - 3 == 4, "spec 自由度 = 4")

spec = USLModel(*PUB_SPEC)
ck(spec.has_peak, "β>0 -> 存在有限峰值")
nmax, xmax = spec.peak()
ck(abs(nmax - 96.5) < 0.05, f"spec Nmax {nmax:.3f} ≈ 96.5")
ck(abs(xmax - 1880) < 5.0, f"spec Xmax {xmax:.1f} ≈ 1880")
ck(abs(spec.limit() - 3245) < 5.0, f"spec limit {spec.limit():.1f} ≈ 3245")
nopt_s, xopt_s = spec.optimal()
ck(abs(nopt_s - 36.1) < 0.05, f"spec Nopt {nopt_s:.3f} ≈ 36.1")
ck(abs(xopt_s - 1540) < 3.0, f"spec Xopt {xopt_s:.1f} ≈ 1540")

# Nmax 公式 vs 数值扫描的 argmax
best_n, best_x = None, -1.0
i = 1.0
while i <= 400.0:
    v = spec.X(i)
    if v > best_x:
        best_x, best_n = v, i
    i += 0.01
ck(abs(best_n - nmax) < 0.05, f"数值 argmax {best_n:.2f} == 公式 Nmax {nmax:.2f}")
ck(abs(best_x - xmax) < 0.5, "数值最大吞吐 == X(Nmax)")

# retrograde：过峰之后吞吐真的往下掉
ck(spec.X(216) < spec.X(nmax), "N=216 的吞吐低于峰值")
ck(spec.X(400) < spec.X(216), "负载再加，吞吐继续下降")
ck(SPECDM91[-1][1] < SPECDM91[3][1], "实测数据本身就出现了倒退")

# what-if：β 减半到 0.00005 -> 峰值外推
wi = USLModel(PUB_SPEC[0], PUB_SPEC[1], 0.00005)
ck(abs(wi.peak()[0] - 139.4) < 0.2, f"β 减半后 Nmax {wi.peak()[0]:.2f} ≈ 139.4")
ck(wi.peak()[0] > nmax, "降低 coherency 会把峰值推远")
ck(wi.peak()[1] > xmax, "降低 coherency 也会抬高峰值吞吐")

# ----------------------------------------------------- 5. 效率（R 的口径）
ck(abs(ray_pub.efficiency(1, 20.0) - 0.915) < 0.001, "ray 效率最大 0.915")
ck(abs(ray_pub.efficiency(64, 310.0) - 0.222) < 0.001, "ray 效率最小 0.222")
ck(abs(spec.efficiency(1, 64.9) - 0.7211) < 0.001, "spec 效率最大 0.7211")
ck(abs(spec.efficiency(216, 1702.2) - 0.0876) < 0.001, "spec 效率最小 0.0876")
# 效率随负载单调下降
prev = 2.0
for n_i, y in SPECDM91:
    e = spec.efficiency(n_i, y)
    ck(e <= prev, f"spec 效率在 N={n_i} 不回升")
    prev = e

# ---------------------------------------------- 6. 参数边界与异常输入保护
try:
    usl_X(10, 1.0, 1.0, 1.0)
    ck(True, "α=β=1 仍可计算")
except ValueError:
    ck(False, "α=β=1 应可计算")
try:
    usl_X(10, 1.0, -0.5, 0.0)      # 分母 1 - 4.5 < 0
    ck(False, "负 α 使分母为负应报错")
except ValueError:
    ck(True, "分母非正报 ValueError")
try:
    fit([(1, 1.0), (2, 2.0), (3, 3.0)])
    ck(False, "数据点不足应报错")
except ValueError:
    ck(True, "数据点不足报 ValueError")

# γ 不参与 Nmax 的定位（Gunther：γ 只缩放 y 轴）
a1 = USLModel(10.0, 0.1, 0.001).peak()[0]
a2 = USLModel(500.0, 0.1, 0.001).peak()[0]
ck(close(a1, a2), "Nmax 与 γ 无关")
ck(USLModel(500.0, 0.1, 0.001).peak()[1] >
   USLModel(10.0, 0.1, 0.001).peak()[1], "Xmax 与 γ 成正比")

print(f"assertions: {N}, failed: {len(FAILED)}")
for f in FAILED:
    print("FAILED:", f)
sys.exit(1 if FAILED else 0)
