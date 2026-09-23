"""演示入口：同一个 P，Amdahl 与 Gustafson 给出差一个数量级的答案。"""

import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from scaling import (  # noqa: E402
    amdahl,
    amdahl_limit,
    efficiency,
    gustafson,
    parallel_fraction_for,
    serial_time_share,
    usl_capacity,
)


def main() -> int:
    print("== 1. Amdahl（强扩展：问题固定，加处理器）==")
    for P in (0.5, 0.9, 0.95, 0.99):
        row = "  ".join(f"N={n:<5}:{amdahl(P, n):7.2f}" for n in (2, 8, 64, 1024))
        print(f"  P={P:<5} 上限 {amdahl_limit(P):>7.1f}x   {row}")

    print("\n== 2. Gustafson（弱扩展：问题随处理器放大）==")
    for P in (0.5, 0.9, 0.95, 0.99):
        row = "  ".join(f"N={n:<5}:{gustafson(P, n):7.2f}" for n in (2, 8, 64, 1024))
        print(f"  P={P:<5} 无上限          {row}")

    print("\n== 3. 同一个 P=0.95 的两个答案 ==")
    print(f"  Amdahl 上限    : {amdahl_limit(0.95):.1f}x")
    print(f"  Gustafson N=1000: {gustafson(0.95, 1000):.1f}x")

    print("\n== 4. 效率（S/N）==")
    for P in (0.9, 0.95, 0.99):
        e = [efficiency(amdahl(P, n), n) for n in (8, 64, 1024)]
        print(f"  P={P}: N=8 {e[0]:.3f}  N=64 {e[1]:.3f}  N=1024 {e[2]:.3f}")

    print("\n== 5. 串行占比：为什么加核最终没用 ==")
    for n in (1, 4, 16, 64, 256, 1024):
        print(f"  N={n:<5} P=0.9 时串行占 wallclock {serial_time_share(0.9, n):.3%}")

    print("\n== 6. 要达到目标加速比，需要多少并行比例 ==")
    for target in (10, 50, 90):
        print(f"  100 进程上 {target:>2}x -> P = {parallel_fraction_for(target, 100):.5f}")

    print("\n== 7. USL 退化：β=0,γ=1 时就是 Amdahl ==")
    for n in (2, 8, 64, 1024):
        print(f"  N={n:<5} Amdahl(f=0.1)={amdahl(0.9, n):8.4f}  "
              f"USL(α=0.1,β=0)={usl_capacity(n, 0.1, 0.0, 1.0):8.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
