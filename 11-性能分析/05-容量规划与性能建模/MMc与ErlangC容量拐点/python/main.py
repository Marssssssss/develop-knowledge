"""演示入口：Erlang B/C、M/M/c 容量拐点、M/G/1 方差惩罚、M/M/1/K 丢弃语义。"""

import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from queuemodel import (  # noqa: E402
    MM1K,
    MMC,
    compare_configurations,
    erlang_b,
    erlang_c,
    erlang_c_via_b,
    mg1,
)


def main() -> int:
    print("== 1. Erlang B/C（offered load E，m 台）==")
    for E, m in ((0.5, 1), (1.0, 2), (2.0, 3), (4.0, 5)):
        print(f"  E={E:<4} m={m:<2} B={erlang_b(E, m):.6f}  "
              f"C={erlang_c(E, m):.6f} (via B: {erlang_c_via_b(E, m):.6f})")

    print("\n== 2. M/M/c 容量拐点（mu=5，E=1.6 erlang 固定，加机器）==")
    for c in (2, 3, 4, 8):     # c=1 时 lam=8 > mu=5，模型直接判不稳定
        m = MMC(lam=8, mu=5, c=c)
        print(f"  c={c}: rho={m.rho:.3f} C={m.C:.4f} "
              f"Wq={m.Wq*1000:7.2f}ms W={m.W*1000:7.2f}ms L={m.L:.3f}")

    print("\n== 3. 同样总能力：c 台慢的 vs 1 台快的 ==")
    for c in (2, 4, 8):
        w_many, w_one = compare_configurations(lam=c * 4, mu=5, c=c)
        print(f"  c={c}: W(c 台各 5/s)={w_many*1000:.2f}ms, "
              f"W(1 台 {c*5}/s)={w_one*1000:.2f}ms")

    print("\n== 4. M/G/1：服务时间方差的代价（lam=500, mu=1000）==")
    for name, v in (("M/D/1 (Var=0)", 0.0),
                    ("M/M/1 (Var=1/mu^2)", 1.0 / 1e6),
                    ("Var=9/mu^2", 9.0 / 1e6)):
        Wq, W, L = mg1(500, 1000, v)
        print(f"  {name:<20} Wq={Wq*1000:6.3f}ms W={W*1000:6.3f}ms L={L:.3f}")

    print("\n== 5. M/M/1/K：客满即丢弃 ==")
    for K in (1, 2, 5, 10, 40):
        k = MM1K(500, 1000, K)
        print(f"  K={K:<3} p_block={k.p_block:.6f} "
              f"lambda_a={k.lambda_a:8.3f} L={k.L:.4f} W={k.W*1000:.3f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
