"""DNS 缓存投毒（伪造应答）与 RFC 5452 的组合难度模型。

RFC 5452《Measures for Making DNS More Resilient against Forged Answers》
§7.1 定义符号 I / P / N / F / R / W / D，§7.2 给出：

    P_s   = D * F / (N * P * I)        F = R * W
    P_cs  = 1 - (1 - P_s) ** A         A = T / TTL

并声明代入常用值后化简为 P_cs = 1 - (1 - R/1638400) ** (T/TTL)。

本模块把公式、化简常数与文档里给出的每一组数字都做成可实跑的断言；
文档自身存在多处口径不一致（§7 vs §8.1），一律**记录不改正**。
"""

# ---------------------------------------------------------------- 核心公式

def p_s(fake_packets, ns_count=2.5, ports=1, id_space=65536, outstanding=1):
    """单次窗口内被投毒的概率 P_s = D*F/(N*P*I)。"""
    return outstanding * fake_packets / float(ns_count * ports * id_space)


def p_s_from_rate(rate_pps, window_s=0.1, ns_count=2.5, ports=1,
                  id_space=65536, outstanding=1):
    """F = R*W 后的 P_s。"""
    return p_s(rate_pps * window_s, ns_count, ports, id_space, outstanding)


def p_cs(period_s, ttl_s, rate_pps=None, window_s=0.1, ns_count=2.5, ports=1,
         id_space=65536, outstanding=1, p_single=None, attempts=None):
    """组合成功概率 P_cs = 1 - (1 - P_s) ** A，A = T/TTL。

    TTL == 0（或 None）时按 §7.2 的退化口径：有效 TTL 取 W。
    """
    if p_single is None:
        p_single = p_s_from_rate(rate_pps, window_s, ns_count, ports,
                                 id_space, outstanding)
    if attempts is None:
        eff_ttl = ttl_s if ttl_s else window_s
        attempts = period_s / float(eff_ttl)
    return 1.0 - (1.0 - p_single) ** attempts


def reduced_constant(ns_count=2.5, ports=1, id_space=65536, window_s=0.1,
                     outstanding=1):
    """§7.2 化简后的分母：N*P*I/(D*W) —— 文档给的是 1638400。"""
    return ns_count * ports * id_space / (outstanding * window_s)


# ---------------------------------------------------------------- 反解

def period_for_probability(target, ttl_s, rate_pps=None, window_s=0.1,
                           ns_count=2.5, ports=1, id_space=65536,
                           outstanding=1, p_single=None):
    """达到 target 组合概率所需的时间（秒）。

    用精确公式反解：A = ln(1-target)/ln(1-P_s)，T = A * 有效TTL。
    """
    import math
    if p_single is None:
        p_single = p_s_from_rate(rate_pps, window_s, ns_count, ports,
                                 id_space, outstanding)
    eff_ttl = ttl_s if ttl_s else window_s
    attempts = math.log(1.0 - target) / math.log(1.0 - p_single)
    return attempts * eff_ttl


def rate_for_probability(target, period_s, ttl_s, window_s=0.1, ns_count=2.5,
                         ports=1, id_space=65536, outstanding=1):
    """达到 target 组合概率所需的发包速率（pps），精确公式反解。"""
    eff_ttl = ttl_s if ttl_s else window_s
    attempts = period_s / float(eff_ttl)
    p_single = 1.0 - (1.0 - target) ** (1.0 / attempts)
    return p_single * ns_count * ports * id_space / (outstanding * window_s)


# ---------------------------------------------------------------- 带宽换算

DNS_RESPONSE_BYTES = 80


def bandwidth_bps(rate_pps, packet_bytes=DNS_RESPONSE_BYTES):
    return rate_pps * packet_bytes * 8.0


def bps_to_human(v):
    for unit, div in (("Gb/s", 1e9), ("Mbit/s", 1e6), ("kbit/s", 1e3)):
        if v >= div:
            return v / div, unit
    return v, "bit/s"


# ---------------------------------------------------------------- 常用派生量

def mean_tries_single_shot(ns_count=2.5, ports=1, id_space=65536):
    """§7 首段：匹配 ID + 源/目的地址平均需要的包数 = N*P*I/2。"""
    return ns_count * ports * id_space / 2.0


def birthday_p_s(base_p_s, outstanding):
    """§5 生日攻击：D 个相同的在途查询让每个伪造包的命中机会线性提高。

    文档明确「This assumption only holds for small values of 'D'」，
    所以这里就是线性放大，不做成 1-(1-p)^D。
    """
    return base_p_s * outstanding


# ---------------------------------------------------------------- 防御项

def problem_space(ns_count=2.5, ports=1, id_space=65536):
    return ns_count * ports * id_space


def port_randomization_gain(ports=64000):
    """源端口随机化把问题空间放大的倍数。"""
    return ports
