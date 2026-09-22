#!/usr/bin/env python3
"""把 deadline 传播与预算拆分跑成现象。运行：``python main.py``"""

from deadline import (
    CANCELLED, DEADLINE_EXCEEDED, OK, client_status, consumed, propagate,
    server_status, split_by_cost, split_by_headroom, split_equal, to_deadline, to_timeout,
)


def demo_point_in_time():
    print("== 1. deadline 是时刻，timeout 是时长 ==")
    t0 = 1_700_000_000.000
    deadline = t0 + 2.0
    print(f"  发起 t0={t0:.3f}  要求 2s 内完成 -> deadline={deadline:.3f}")
    for now in (t0, t0 + 0.5, t0 + 1.5, t0 + 2.5):
        print(f"    now=+{now - t0:4.1f}s  timeout={to_timeout(deadline, now):5.2f}s  "
              f"client={client_status(deadline, now + 0.01):<18} server={server_status(deadline, now)}")


def demo_official_timeline():
    print("\n== 2. 官方时序：2s 预算，本跳花 0.5s，下游只拿 1.5s ==")
    deadline = 1000.0 + 2.0          # 13:00:02 的抽象
    now_after_half = 1000.0 + 0.5
    print(f"  上游 deadline={deadline}  本跳已到 now={now_after_half}")
    print(f"  propagate(0 预留) -> 下游 timeout={propagate(deadline, now_after_half):.2f}s")
    print(f"  propagate(留 0.2s 收尾) -> 下游 timeout={propagate(deadline, now_after_half, 0.2):.2f}s")


def demo_clock_skew():
    print("\n== 3. 时钟偏移：线路上传 timeout 才免疫 ==")
    client_now = 1000.0
    deadline_client = client_now + 2.0
    wire_timeout = to_timeout(deadline_client, client_now)
    for skew in (-30.0, 0.0, 30.0):
        server_now = client_now + skew          # 服务端时钟快/慢 30s
        local_deadline = to_deadline(wire_timeout, server_now)
        naive_remaining = deadline_client - server_now   # 直接传绝对时刻的错法
        print(f"  时钟偏 {skew:+6.1f}s -> 正确剩余={local_deadline - server_now:5.2f}s  "
              f"（直传时刻会算出 {naive_remaining:+7.2f}s）")


def demo_budget_split():
    print("\n== 4. 200ms SLO 拆给 3 跳 ==")
    total = 200.0
    print(f"  等分           : {[round(x, 1) for x in split_equal(total, 3)]}")
    costs = [120.0, 50.0, 30.0]
    print(f"  按成本加权     : {[round(x, 1) for x in split_by_cost(total, costs)]}  (成本 {costs})")
    res = split_by_headroom(total, floors=[40.0, 20.0, 10.0], caps=[150.0, 60.0, 30.0])
    print(f"  可压缩空间注水 : {[round(x, 1) for x in res['alloc']]}  "
          f"余量={res['slack']:.1f}ms 可行={res['feasible']}")
    bad = split_by_headroom(50.0, floors=[40.0, 20.0, 10.0], caps=[150.0, 60.0, 30.0])
    print(f"  下限之和>预算  : feasible={bad['feasible']} slack={bad['slack']:.0f}ms  <- 必须降级或砍依赖")


def demo_reconcile():
    print("\n== 5. 逐跳对账：谁超了预算 ==")
    alloc = split_by_cost(200.0, [120.0, 50.0, 30.0])
    actual = [140.0, 45.0, 32.0]
    for i, r in enumerate(consumed(alloc, actual), 1):
        flag = "超支" if r["over"] > 0 else "达标"
        print(f"  跳{i}: 预算={r['budget']:6.1f}ms 实际={r['actual']:6.1f}ms "
              f"差={r['over']:+6.1f}ms  {flag}")


if __name__ == "__main__":
    demo_point_in_time()
    demo_official_timeline()
    demo_clock_skew()
    demo_budget_split()
    demo_reconcile()
    print(f"\n状态常量: OK={OK} DEADLINE_EXCEEDED={DEADLINE_EXCEEDED} CANCELLED={CANCELLED}")
