"""演示入口：Little 定律的三种用法 + M/M/1 拐点 + 死队列 + 仿真核对。"""

import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from little import (  # noqa: E402
    MM1,
    dead_queue,
    knee_table,
    little_l,
    pool_size,
    simulate_mm1,
    throughput_ceiling,
)


def main() -> int:
    print("== 1. Little 定律三用 ==")
    print(f"  定并发: lambda=500rps, W=200ms -> L = {little_l(lam=500, W=0.2):.0f}")
    print(f"  找隐性等待: L=50, lambda=100rps -> W = {little_l(L=50, lam=100)*1000:.0f}ms"
          f"（服务 50ms，其余 {little_l(L=50, lam=100)*1000-50:.0f}ms 在排队）")
    print(f"  吞吐上限: S=10ms 单 worker -> {throughput_ceiling(0.01):.0f}/s")

    print("\n== 2. M/M/1 拐点（S=100ms）==")
    for rho, mul, w in knee_table(S=0.1):
        print(f"  rho={rho:>5.2f}  倍率 {mul:>7.2f}x  W = {w*1000:>8.1f}ms")

    print("\n== 3. 死队列判据 ==")
    wasted, useful = dead_queue(10000, 500, 2.0)
    print(f"  深 10000、500/s 排空、2s 超时 -> 作废 {wasted}，有效 {useful}")

    print("\n== 4. 事件驱动仿真核对 Little 定律 ==")
    for lam in (500, 800):
        L_hat, lam_hat, W_hat, n = simulate_mm1(lam, 1000, t_end=20000.0)
        m = MM1(lam, 1000)
        print(f"  lambda={lam}: L_hat={L_hat:.4f} (解析 {m.L:.4f}), "
              f"lambda_hat*W_hat={lam_hat*W_hat:.4f}, n={n}")

    print("\n== 5. 池子尺寸 ==")
    print(f"  500rps x 200ms -> {pool_size(500, 0.2)} 并发"
          f"（1.5 倍余量 {pool_size(500, 0.2, 1.5)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
