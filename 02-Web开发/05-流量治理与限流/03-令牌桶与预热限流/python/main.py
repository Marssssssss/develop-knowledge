"""演示入口：Bursty 的突发额度与 WarmingUp 的冷启动代价曲线。"""

from guava_ratelimiter import SmoothBursty, SmoothWarmingUp

QPS = 10.0


def main():
    print("== SmoothBursty(qps=%g)：闲置多久攒多少突发 ==" % QPS)
    for idle_ms in (0, 100, 500, 1000, 3000):
        b = SmoothBursty()
        b.set_rate(QPS, 0)
        b.acquire(1, 0)                      # 先花掉一次，让 nextFreeTicket 进入未来
        b.resync(idle_ms * 1000)
        print("  闲置 %5dms → stored=%5.2f / max=%.1f" % (idle_ms, b.stored_permits, b.max_permits))

    print("\n== SmoothWarmingUp(qps=%g, warmup=1s, coldFactor=3.0) 的冷启动代价 ==" % QPS)
    w = SmoothWarmingUp(warmup_micros=1_000_000)
    w.set_rate(QPS, 0)
    print("  thresholdPermits=%.2f maxPermits=%.2f slope=%.0f coolDown=%.0fus"
          % (w.threshold_permits, w.max_permits, w.slope, w.cool_down_interval_micros()))
    for take in (1.0, 2.5, 5.0, 7.5, 10.0):
        cost = w.stored_permits_to_wait_time(10.0, take)
        print("  从满桶取 %4.1f 张 → 等待 %8.1f ms" % (take, cost / 1000.0))

    print("\n== 同为 10 qps，连续取 5 张的累计等待（Bursty vs WarmingUp）==")
    bb = SmoothBursty()
    bb.set_rate(QPS, 0)
    bb.resync(1_000_000)
    ww = SmoothWarmingUp(warmup_micros=1_000_000)
    ww.set_rate(QPS, 0)
    total_b = sum(bb.acquire(1, 1_000_000) for _ in range(5))
    total_w = ww.stored_permits_to_wait_time(ww.stored_permits, 5.0) / 1e6
    print("  Bursty 累计 %6.3f s（存储令牌免费）" % total_b)
    print("  WarmingUp 累计 %6.3f s（左半段按 stableInterval 计价）" % total_w)


if __name__ == "__main__":
    main()
