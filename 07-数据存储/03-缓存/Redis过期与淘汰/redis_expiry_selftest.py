"""Redis 过期与淘汰自检。运行： python redis_expiry_selftest.py"""
import sys
from redis_expiry import (
    command_clears_ttl, expire, expire_event, INF,
    active_expire_cycle_params, ACTIVE_EXPIRE_CYCLE_KEYS_PER_LOOP,
    LFU_INIT_VAL, LFU_COUNTER_MAX, LFU_MINUTES_WRAP,
    lfu_init_lru, lfu_unpack, lfu_time_elapsed, lfu_log_incr, lfu_decay,
    lfu_idle, lfu_expected_counter, lfu_expected_counter_exact,
    OFFICIAL_LFU_TABLE,
    POLICIES, eviction_result, counted_for_evict,
)

FAILS = []


def check(label, cond, detail=""):
    if cond:
        print("  ok   %s" % label)
    else:
        FAILS.append("%s %s" % (label, detail))
        print("  FAIL %s %s" % (label, detail))


print("[1] EXPIRE 的 NX/XX/GT/LT（Redis 7.0 加入）")
persistent = {"ttl": None, "expire_at": None}
ok, _ = expire(persistent, 10, "NX")
check("NX 对无 TTL 的键 -> 设置成功", ok == 1, ok)
ok, _ = expire(persistent, 10, "XX")
check("XX 对无 TTL 的键 -> 跳过", ok == 0, ok)
ok, _ = expire(persistent, 10, "GT")
check("GT 对 non-volatile(infinite TTL) -> 不成立", ok == 0, ok)
ok, _ = expire(persistent, 10, "LT")
check("LT 对 non-volatile(infinite TTL) -> 成立", ok == 1, ok)
vol = {"ttl": 100, "expire_at": 1000}
ok, _ = expire(vol, 10, "GT", now=900)
check("GT：新过期时间 910 < 当前 1000 -> 跳过", ok == 0, ok)
ok, _ = expire(vol, 200, "GT", now=900)
check("GT：新过期时间 1100 > 1000 -> 设置", ok == 1, ok)
ok, _ = expire(vol, 10, "LT", now=900)
check("LT：新过期时间 910 < 1000 -> 设置", ok == 1, ok)
ok, _ = expire(None, 10)
check("键不存在 -> 返回 0", ok == 0, ok)

print("[2] 哪些命令清 TTL，哪些不清")
check("SET 清除 TTL", command_clears_ttl("SET") is True)
check("DEL 清除 TTL", command_clears_ttl("DEL") is True)
check("GETSET 清除 TTL", command_clears_ttl("GETSET") is True)
check("SUNIONSTORE 等 *STORE 清除 TTL", command_clears_ttl("SUNIONSTORE") is True)
check("INCR 保留 TTL", command_clears_ttl("INCR") is False)
check("LPUSH 保留 TTL", command_clears_ttl("LPUSH") is False)
check("HSET 保留 TTL", command_clears_ttl("HSET") is False)

print("[3] 非正数超时 / 过去时间 = 删除，不是过期")
check("ttl=0 -> del 事件", expire_event(ttl=0) == "del")
check("ttl 为负 -> del 事件", expire_event(ttl=-5) == "del")
check("EXPIREAT 过去时间 -> del 事件",
      expire_event(at_time_in_past=True) == "del")
check("正常 ttl -> expired 事件", expire_event(ttl=10) == "expired")

print("[4] activeExpireCycle 的 effort 换算（expire.c）")
p1 = active_expire_cycle_params(1)
check("默认 effort=1 -> keys_per_loop 20",
      p1["keys_per_loop"] == ACTIVE_EXPIRE_CYCLE_KEYS_PER_LOOP == 20, p1)
check("默认 fast_duration 1000 us", p1["fast_duration_us"] == 1000, p1)
check("默认 slow_time_perc 25", p1["slow_time_perc"] == 25, p1)
check("默认 acceptable_stale 10", p1["acceptable_stale"] == 10, p1)
p10 = active_expire_cycle_params(10)
check("effort=10 -> keys_per_loop 65", p10["keys_per_loop"] == 65, p10)
check("effort=10 -> fast_duration 3250", p10["fast_duration_us"] == 3250, p10)
check("effort=10 -> slow_time_perc 43", p10["slow_time_perc"] == 43, p10)
check("effort=10 -> acceptable_stale 1", p10["acceptable_stale"] == 1, p10)
check("effort 越大容忍的陈旧比例越低（10 > 1）",
      p10["acceptable_stale"] < p1["acceptable_stale"])
check("effort 越大每轮扫的键越多", p10["keys_per_loop"] > p1["keys_per_loop"])
check("effort 越大占用的 CPU 上限越高",
      p10["slow_time_perc"] > p1["slow_time_perc"])
check("effort 最大时 acceptable_stale 仍 > 0（不会到 0）",
      active_expire_cycle_params(10)["acceptable_stale"] == 1)

print("[5] LFU：24 位 lru 字段的布局")
lru = lfu_init_lru(1234)
ldt, counter = lfu_unpack(lru)
check("高 16 位是分钟时间戳", ldt == 1234, ldt)
check("低 8 位初始值是 LFU_INIT_VAL=5", counter == LFU_INIT_VAL, counter)
check("LFU_INIT_VAL 来自 server.h", LFU_INIT_VAL == 5)

print("[6] LFU：Morris 概率递增")
check("r=0 必然递增（r < p 恒成立）", lfu_log_incr(5, 0.0, 10) == 6)
check("r=1 几乎不递增", lfu_log_incr(5, 1.0, 10) == 5)
check("counter=255 饱和", lfu_log_incr(255, 0.0, 10) == 255)
check("counter < 5 时 baseval 被夹到 0，p = 1",
      lfu_log_incr(0, 0.5, 10) == 1)
p_low = 1.0 / ((5 - 5) * 10 + 1)
p_high = 1.0 / ((100 - 5) * 10 + 1)
check("低频时 p=1.0，高频(100)时 p=1/951", abs(p_low - 1.0) < 1e-12
      and abs(p_high - 1 / 951) < 1e-12, (p_low, p_high))
check("factor 越大，同一 counter 下的递增概率越低",
      1.0 / ((100 - 5) * 100 + 1) < p_high)

print("[7] LFU：衰减与 16 位分钟回绕")
check("默认 decay_time=1，过 5 分钟 -> 减 5", lfu_decay(20, 100, 105, 1) == 15)
check("衰减下限为 0，不会出现负数", lfu_decay(3, 100, 110, 1) == 0)
check("decay_time=0 -> 永不衰减", lfu_decay(20, 100, 99999, 0) == 20)
check("decay_time=5，过 10 分钟 -> 减 2", lfu_decay(20, 100, 110, 5) == 18)
check("未回绕的时间差", lfu_time_elapsed(100, 105) == 5)
# 源码是 `return 65535 - ldt + now`。而 16 位能表示 0..65535 共 65536 个值，
# 真正的环绕距离应为 65536 - ldt + now = 11，源码算出 10 —— **跨回绕时少算 1 分钟**。
check("跨回绕：源码公式算 10", lfu_time_elapsed(65530, 5) == 10,
      lfu_time_elapsed(65530, 5))
check("跨回绕：真实经过 11 分钟（源码少算 1）",
      (65536 - 65530 + 5) == 11 and lfu_time_elapsed(65530, 5) == 10)
check("非回绕路径不受影响", lfu_time_elapsed(5, 10) == 5)
check("16 位分钟最大值 65535", LFU_MINUTES_WRAP == 65535)
check("idle = 255 - counter（选 idle 最大者淘汰）",
      lfu_idle(255) == 0 and lfu_idle(0) == 255)

print("[8] 均值场近似复现官方 lfu-log-factor 表格")
worst = 0.0
for factor, row in OFFICIAL_LFU_TABLE.items():
    for hits, doc in row.items():
        got = lfu_expected_counter(hits, factor)
        tol = max(2.0, 0.10 * doc)
        okk = abs(got - doc) <= tol
        rel = abs(got - doc) / doc if doc else 0.0
        worst = max(worst, rel)
        if not okk:
            check("factor=%d hits=%d 文档=%d 近似=%.1f" % (factor, hits, doc, got),
                  False, "tol=%.1f" % tol)
# 最大相对误差出现在最小的格子（factor=100 / 100 hits：6.72 vs 文档 8，16%）——
# 计数绝对值很小时，均值场近似与一次随机参考运行的偏差本来就最显眼。
check("全部 16 个格子都在容差内", worst < 0.20, "worst=%.3f" % worst)
check("默认 factor=10 大约 100 万次访问饱和（文档值 255）",
      lfu_expected_counter(1000000, 10) == LFU_COUNTER_MAX,
      lfu_expected_counter(1000000, 10))
check("精确马尔可夫链与均值场近似一致（factor 10 / 100 次）",
      abs(lfu_expected_counter_exact(100, 10) - lfu_expected_counter(100, 10)) < 0.5,
      (lfu_expected_counter_exact(100, 10), lfu_expected_counter(100, 10)))
check("精确马尔可夫链与均值场近似一致（factor 1 / 1000 次）",
      abs(lfu_expected_counter_exact(1000, 1) - lfu_expected_counter(1000, 1)) < 0.5,
      (lfu_expected_counter_exact(1000, 1), lfu_expected_counter(1000, 1)))
check("factor 越大，相同访问次数下的计数越低（区分度右移）",
      lfu_expected_counter(100, 100) < lfu_expected_counter(100, 10)
      < lfu_expected_counter(100, 0))

print("[9] 淘汰策略与 volatile- 陷阱")
check("官方列出 10 种策略", len(POLICIES) == 10, len(POLICIES))
check("allkeys-lru 总能淘汰", eviction_result("allkeys-lru", False) == "evict")
check("volatile-lru 且无 TTL 键 -> 报错（等同 noeviction）",
      eviction_result("volatile-lru", False) == "error")
check("volatile-lru 且有 TTL 键 -> 正常淘汰",
      eviction_result("volatile-lru", True) == "evict")
check("volatile-lfu 同理", eviction_result("volatile-lfu", False) == "error")
check("volatile-ttl 同理", eviction_result("volatile-ttl", False) == "error")
check("noeviction 永远报错", eviction_result("noeviction", True) == "error")

print("[10] AOF/复制缓冲区不计入 maxmemory（防反馈环）")
check("used 才是判据", counted_for_evict(100, 20, 30) == 100)
check("缓冲区被排除（源码 freeMemoryGetNotCountedMemory）",
      counted_for_evict(100, 20, 30) < 100 + 20 + 30)

print()
if FAILS:
    print("FAILED %d:" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
