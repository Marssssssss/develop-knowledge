"""DNS 缓存投毒：把 RFC 5452 §7/§8 的每一组数字跑出来对照。"""

from dnspoison import (
    p_cs, p_s_from_rate, reduced_constant, period_for_probability,
    rate_for_probability, bandwidth_bps, mean_tries_single_shot,
    problem_space, DNS_RESPONSE_BYTES,
)

DAY, HOUR = 86400.0, 3600.0
R = 7000.0

print("== 1. §7.2 化简常数 ==")
print("   N*P*I/(D*W) = %.0f（文档：1638400）" % reduced_constant())
print("   N=1 时      = %.0f（§8.1 实际用的是它）" % reduced_constant(ns_count=1.0))
print("   P=64000 时  = %.0f" % reduced_constant(ports=64000))

print("\n== 2. §8 TTL=3600 / R=7000 pps ==")
for label, t in (("24 小时", DAY), ("7 天", 7 * DAY)):
    print("   %-6s -> P_cs = %.4f" % (label, p_cs(t, 3600.0, rate_pps=R)))

print("\n== 3. §8 TTL=60 / R=7000 pps ==")
for label, t in (("24 分钟", 1440.0), ("3 小时", 3 * HOUR), ("9 小时", 9 * HOUR)):
    print("   %-6s -> P_cs = %.4f" % (label, p_cs(t, 60.0, rate_pps=R)))

print("\n== 4. §7 单次窗口所需带宽 ==")
print("   N=2  平均需 %.0f 包；N=2.5 平均需 %.0f 包" % (
    mean_tries_single_shot(ns_count=2.0), mean_tries_single_shot(ns_count=2.5)))
print("   65000 包 / 0.1s = %.1f Mbit/s（文档 416）" % (bandwidth_bps(65000 / 0.1) / 1e6))
print("   65000 包 / 1.0s = %.1f Mbit/s（文档 42）" % (bandwidth_bps(65000 / 1.0) / 1e6))
r_need = rate_for_probability(0.5, HOUR, 300.0, window_s=1.0)
print("   60 分钟 + TTL=300 达 50%%：精确公式需 %.2f Mbit/s（文档说 4）" % (bandwidth_bps(r_need) / 1e6))

print("\n== 5. §8 的 285 Gb/s 是哪一个口径 ==")
print("   285 Gb/s / 64000 端口 = %.2f Mbit/s" % (285e9 / 64000 / 1e6))
print("   416 Mbit/s * 64000    = %.1f Tb/s  ← 不是它" % (bandwidth_bps(65000 / 0.1) * 64000 / 1e12))

print("\n== 6. §8.1 有效 TTL=0（窗口 = W = 0.1s）==")
for ns in (1.0, 2.5):
    t = period_for_probability(0.5, 0, rate_pps=R, ns_count=ns)
    t64 = period_for_probability(0.5, 0, rate_pps=R, ns_count=ns, ports=64000)
    print("   N=%.1f  1 端口 -> %.2f s      64000 端口 -> %.1f 小时" % (ns, t, t64 / HOUR))

print("\n== 7. 生日攻击（§5）：D 个相同在途查询 ==")
base = p_s_from_rate(R, 0.1)
for d in (1, 2, 4, 8, 16):
    print("   D=%-3d P_s = %.6f（%.1f 倍）" % (d, base * d, d))

print("\n== 8. 问题空间 ==")
print("   默认 N*P*I = %.0f；端口随机化后 = %.0f（×64000）" % (
    problem_space(), problem_space(ports=64000)))
