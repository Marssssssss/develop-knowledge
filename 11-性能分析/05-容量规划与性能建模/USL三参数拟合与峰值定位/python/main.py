"""演示入口：拟合两个公开数据集，复现 R 包 usl 的系数与峰值结论。"""

import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from uslmodel import (  # noqa: E402
    RAYTRACER,
    SPECDM91,
    USLModel,
    fit,
    residual_std_error,
    sse,
)


def report(name, data, published):
    m, s = fit(data)
    print(f"== {name} ==")
    print(f"  拟合   : alpha={m.alpha:.7f}  beta={m.beta:.7f}  gamma={m.gamma:.6f}")
    print(f"  已发表 : alpha={published[1]:.7f}  beta={published[2]:.7f}  "
          f"gamma={published[0]:.6f}")
    print(f"  SSE={s:.4f}  RSE={residual_std_error(data, m.gamma, m.alpha, m.beta):.3f} "
          f"(df={len(data)-3})")
    pub = USLModel(*published)
    print(f"  峰值   : {'无（beta=0）' if not pub.has_peak else f'{pub.peak()[0]:.2f}'}"
          f"   Amdahl 渐近线 Xlim={pub.limit():.1f}")
    nopt, xopt = pub.optimal()
    print(f"  最优点 : N={nopt:.2f}  X={xopt:.1f}")
    print(f"  效率   : min={min(pub.efficiency(N, y) for N, y in data):.4f}  "
          f"max={max(pub.efficiency(N, y) for N, y in data):.4f}")


def main() -> int:
    report("raytracer（BRL-CAD 光线追踪，1~64 处理器）", RAYTRACER,
           (21.848843, 0.057771, 0.0))
    print()
    report("specsdm91（SPEC SDM91，1~216 虚拟用户）", SPECDM91,
           (89.9952382, 0.0277285, 0.0001044))

    print("\n== what-if：把 coherency 系数 β 减半 ==")
    base = USLModel(89.9952382, 0.0277285, 0.0001044)
    for beta in (0.0001044, 0.00005, 0.00002):
        w = USLModel(89.9952382, 0.0277285, beta)
        nmax, xmax = w.peak()
        print(f"  beta={beta:<9} Nmax={nmax:8.2f}  Xmax={xmax:8.1f}")

    print("\n== 外推（specsdm91 模型）==")
    for N in (72, 96.5, 144, 216, 300):
        print(f"  N={N:<6} X(N)={base.X(N):8.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
