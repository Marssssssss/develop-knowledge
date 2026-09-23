"""演示入口：关键性降级 / 客户端自适应限流稳态 / Envoy 梯度 / Netflix Vegas。"""

import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from overload import (  # noqa: E402
    CRITICAL,
    CRITICALITY_ORDER,
    CRITICAL_PLUS,
    SHEDDABLE,
    SHEDDABLE_PLUS,
    CriticalityShedder,
    client_throttle_equilibrium,
    envoy_gradient,
    envoy_update,
)
from vegas import VegasLimit  # noqa: E402


def main() -> int:
    print("== 1. 关键性分级降级（SRE 书四个值）==")
    shed = CriticalityShedder({SHEDDABLE: 0.6, SHEDDABLE_PLUS: 0.7,
                               CRITICAL: 0.85, CRITICAL_PLUS: 0.95})
    for u in (0.5, 0.65, 0.75, 0.9, 0.99):
        kept = [c for c in CRITICALITY_ORDER if shed.serves(c, u)]
        print(f"  util={u:<5} 仍在服务: {kept}")

    print("\n== 2. 客户端自适应限流稳态（C=200，G=1000）==")
    for K in (1.1, 1.5, 2.0, 3.0):
        s, a, br, lr = client_throttle_equilibrium(1000, 200, K)
        print(f"  K={K:<4} 发送 {s:7.1f}  后端接受 {a:6.1f}  后端拒绝 {br:6.1f} "
              f" 本地拒绝 {lr:6.1f}  拒:接 = {br/a:.2f}")

    print("\n== 3. Envoy 梯度控制器（minRTT=10ms）==")
    for srtt in (10, 10.5, 11, 15, 20, 50):
        g0 = envoy_gradient(10, srtt)
        g1 = envoy_gradient(10, srtt, buffer_pct=0.1)
        print(f"  sampleRTT={srtt:<5} gradient(无 buffer)={g0:.4f} "
              f"(10% buffer)={g1:.4f}  limit100 -> {envoy_update(100, g1):.2f}")

    print("\n== 4. Netflix VegasLimit（initialLimit=30）==")
    v = VegasLimit(initial_limit=30, jitter_source=lambda: 0.5)
    print(f"  alpha={v.alpha()} beta={v.beta()} threshold={v.threshold()}")
    rtt = 10
    v.update(rtt=rtt, inflight=30)
    for step, r in enumerate((10, 12, 20, 40, 12, 10)):
        new = v.update(rtt=r, inflight=30)
        q = VegasLimit.queue_size(v.estimated_limit, v.rtt_noload, r)
        print(f"  step{step}: rtt={r:<3} queueSize={q:<4} -> limit={new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
