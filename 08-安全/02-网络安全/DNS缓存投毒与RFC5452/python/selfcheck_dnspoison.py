"""DNS 缓存投毒与 RFC 5452 自检（实跑）。"""

import sys
from dnspoison import (
    p_s, p_s_from_rate, p_cs, reduced_constant, period_for_probability,
    rate_for_probability, bandwidth_bps, mean_tries_single_shot,
    birthday_p_s, problem_space, port_randomization_gain, DNS_RESPONSE_BYTES,
)

N_PASS = 0


def ok(cond, msg):
    global N_PASS
    assert cond, "ASSERT FAILED: " + msg
    N_PASS += 1


DAY = 86400.0
HOUR = 3600.0

# ---------------------------------------------------------------- E1 化简常数

ok(abs(reduced_constant() - 1638400.0) < 1e-9,
   "E1a §7.2 化简常数 = N*P*I/(D*W) = 1638400（实测 %r）" % reduced_constant())
ok(reduced_constant(ports=64000) / reduced_constant() == 64000.0,
   "E1b 源端口随机化把分母放大 64000 倍")
ok(reduced_constant(ns_count=1.0) == 655360.0,
   "E1c 只有 1 个权威服务器时化简常数变成 655360（§8.1 用的就是它）")

# ---------------------------------------------------------------- E2 §8 的三组数字

# TTL=3600、7000 pps：24h 约 10%，一周约 50%
p24 = p_cs(DAY, 3600.0, rate_pps=7000)
p168 = p_cs(7 * DAY, 3600.0, rate_pps=7000)
ok(abs(p24 - 0.10) < 0.01, "E2a TTL=3600 / 24h -> %.4f（文档：10%%）" % p24)
ok(abs(p168 - 0.50) < 0.03, "E2b TTL=3600 / 7d  -> %.4f（文档：50%%）" % p168)

# TTL=60：24 分钟 10%，不到 3 小时 50%，约 9 小时 90%
p24m = p_cs(1440.0, 60.0, rate_pps=7000)
p3h = p_cs(3 * HOUR, 60.0, rate_pps=7000)
p9h = p_cs(9 * HOUR, 60.0, rate_pps=7000)
ok(abs(p24m - 0.10) < 0.01, "E2c TTL=60 / 24min -> %.4f（文档：10%%）" % p24m)
ok(p3h > 0.50, "E2d TTL=60 / 3h     -> %.4f（文档：不足 3 小时就到 50%%）" % p3h)
ok(abs(p9h - 0.90) < 0.02, "E2e TTL=60 / 9h     -> %.4f（文档：约 90%%）" % p9h)
# TTL=3600/24h 与 TTL=60/24min 的尝试次数都是 24，概率必然相同
ok(abs(p24 - p24m) < 1e-12, "E2f 两者 A=T/TTL 同为 24，概率恒等")

# ---------------------------------------------------------------- E3 §7 的单次窗口数字

ok(abs(mean_tries_single_shot(ns_count=2.0) - 65536.0) < 1e-9,
   "E3a N=2 时匹配 ID+地址平均需 N*I/2 = 65536 包（文档表述为 2*2^15=65000）")
ok(abs(mean_tries_single_shot(ns_count=2.5) - 81920.0) < 1e-9,
   "E3a' N 取文档默认的 2.5 时是 81920 包")
ok(abs(bandwidth_bps(65000 / 0.1) / 1e6 - 416.0) < 1e-9,
   "E3b 65000 包塞进 100ms -> %.1f Mbit/s（文档：416 Mbit/s）" % (bandwidth_bps(65000 / 0.1) / 1e6))
ok(abs(bandwidth_bps(65000 / 1.0) / 1e6 - 41.6) < 1e-9,
   "E3c 1 秒窗口 -> %.1f Mbit/s（文档取整为 42 Mbit/s）" % (bandwidth_bps(65000 / 1.0) / 1e6))
ok(DNS_RESPONSE_BYTES == 80, "E3d 文档假设的最小应答 80 字节")
ok(abs(bandwidth_bps(650000) / 1e6 - 416.0) < 1e-9,
   "E3e 650000 pps * 80B * 8 = 416 Mbit/s 精确成立")

# 60 分钟 + TTL=300：文档说 4 Mbit/s 够到 50%
r_doc = 4.0e6 / (DNS_RESPONSE_BYTES * 8)
p_doc = p_cs(HOUR, 300.0, rate_pps=r_doc, window_s=1.0)
ok(0.30 < p_doc < 0.45,
   "E3f 4 Mbit/s / 60min / TTL=300(W=1s) 精确公式给 %.4f —— 文档说 50%% 是线性近似" % p_doc)
r_exact = rate_for_probability(0.5, HOUR, 300.0, window_s=1.0)
ok(5.0e6 < bandwidth_bps(r_exact) < 6.5e6,
   "E3g 用§7.2 精确公式反解，50%% 需要 %.2f Mbit/s（而非 4）" % (bandwidth_bps(r_exact) / 1e6))
r_437 = 4.37e6 / (DNS_RESPONSE_BYTES * 8)
ok(abs(12 * p_s_from_rate(r_437, 1.0) - 0.5) < 0.005,
   "E3h 4.37 Mbit/s 时线性近似 12*P_s = %.4f ≈ 0.5 —— 印证文档用的是线性叠加"
   % (12 * p_s_from_rate(r_437, 1.0)))
ok(12 * p_s_from_rate(r_doc, 1.0) < 0.47,
   "E3i 文档把 4.37 取整成 4 Mbit/s 后线性值只剩 %.4f" % (12 * p_s_from_rate(r_doc, 1.0)))

# ---------------------------------------------------------------- E4 §8 的 285 Gb/s

ok(abs(285e9 / 64000.0 / 1e6 - 4.45) < 0.05,
   "E4a 285 Gb/s 除以 64000 端口 = %.2f Mbit/s —— 与 §7 的 4 Mbit/s 同一口径" % (285e9 / 64000.0 / 1e6))
ok(bandwidth_bps(65536 / 0.1) * 64000 > 1e13,
   "E4b 若用单次窗口的 416 Mbit/s 去乘 64000 会到 %.1f Tb/s，与 285 Gb/s 差两个数量级"
   % (bandwidth_bps(65536 / 0.1) * 64000 / 1e12))
ok(bandwidth_bps(65536 / 0.1) * 64000 / 285e9 > 50,
   "E4c 两者相差 %.0f 倍 —— 285 Gb/s 不是从 416 Mbit/s 推出来的" % (bandwidth_bps(65536 / 0.1) * 64000 / 285e9))

# ---------------------------------------------------------------- E5 §8.1 有效 TTL=0

# 文档：1 个源端口 -> 7 秒到 50%；64000 端口 -> 116 小时
t_one_n25 = period_for_probability(0.5, 0, rate_pps=7000, ns_count=2.5)
t_one_n1 = period_for_probability(0.5, 0, rate_pps=7000, ns_count=1.0)
ok(abs(t_one_n25 - 16.2) < 0.5, "E5a N=2.5 时需 %.2f 秒（文档说 7 秒）" % t_one_n25)
ok(abs(t_one_n1 - 7.0) < 1.0, "E5b N=1    时需 %.2f 秒 —— 与文档的 7 秒吻合" % t_one_n1)
ok(t_one_n25 / t_one_n1 > 2.3,
   "E5c §8.1 与 §7.2 的比值 %.2f —— 文档两节用了不同的 N" % (t_one_n25 / t_one_n1))

t_64k_n1 = period_for_probability(0.5, 0, rate_pps=7000, ns_count=1.0, ports=64000)
t_64k_n25 = period_for_probability(0.5, 0, rate_pps=7000, ns_count=2.5, ports=64000)
ok(abs(t_64k_n1 / HOUR - 116.0) < 6.0,
   "E5d N=1 + 64000 端口 -> %.1f 小时（文档：约 116 小时）" % (t_64k_n1 / HOUR))
ok(abs(t_64k_n25 / HOUR - 288.0) < 8.0,
   "E5e N=2.5 + 64000 端口 -> %.1f 小时" % (t_64k_n25 / HOUR))
ok(abs(t_64k_n1 / t_one_n1 - 64000.0) < 500,
   "E5f 端口数从 1 到 64000 把所需时间放大约 %.0f 倍" % (t_64k_n1 / t_one_n1))

# ---------------------------------------------------------------- E6 生日攻击（§5）

base = p_s_from_rate(7000.0, 0.1)
ok(abs(birthday_p_s(base, 1) - base) < 1e-15, "E6a D=1 时退化为原概率")
for d in (2, 4, 8):
    ok(abs(birthday_p_s(base, d) - base * d) < 1e-15, "E6b D=%d 是线性放大" % d)
ok(birthday_p_s(base, 16) > base * 15.9, "E6c D=16 仍是线性（文档只说小 D 成立）")
# 文档：只要伪造包能匹配任一在途查询即可，故 D 直接乘在分子上
ok(abs(p_s_from_rate(7000.0, 0.1, outstanding=8) / base - 8.0) < 1e-12,
   "E6d 公式里的 D 就是线性因子")

# ---------------------------------------------------------------- E7 退化 TTL

ok(abs(p_cs(1.0, 0, rate_pps=7000, window_s=0.1) -
       p_cs(1.0, 0.1, rate_pps=7000, window_s=0.1)) < 1e-12,
   "E7a TTL=0 时有效 TTL 取 W，与显式传 W 等价")
ok(abs(p_cs(1.0, None, rate_pps=7000, window_s=0.1) -
       p_cs(1.0, 0.1, rate_pps=7000, window_s=0.1)) < 1e-12,
   "E7b 传 None 同样走退化分支")

# ---------------------------------------------------------------- E8 单调性与边界

ok(p_cs(DAY, 3600.0, rate_pps=7000) < p_cs(2 * DAY, 3600.0, rate_pps=7000),
   "E8a 时间越长概率越高")
ok(p_cs(DAY, 3600.0, rate_pps=7000) > p_cs(DAY, 3600.0, rate_pps=700),
   "E8b 速率越高概率越高")
ok(p_cs(DAY, 3600.0, rate_pps=7000) > p_cs(DAY, 3600.0, rate_pps=7000, ports=64000),
   "E8c 端口随机化把概率压下去")
ok(p_cs(DAY, 3600.0, rate_pps=0) == 0.0, "E8d 不发伪造包概率为 0")
ok(abs(p_cs(DAY, 3600.0, rate_pps=7000, ns_count=2.5) * 2.5 -
       p_cs(DAY, 3600.0, rate_pps=7000, ns_count=1.0)) < 0.02,
   "E8e 小概率区近似线性：N=1 的概率约为 N=2.5 的 2.5 倍")
ok(problem_space() == 163840.0, "E8f 默认问题空间 N*P*I = 163840")
ok(port_randomization_gain() == 64000, "E8g 端口增益 = 64000")
ok(abs(p_s_from_rate(7000.0, 0.1) * 1638400.0 - 7000.0) < 1e-6,
   "E8h 化简式核对：P_s * 1638400 == R")

print("PASS %d assertions" % N_PASS)
sys.exit(0)
