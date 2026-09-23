"""过载保护自检：关键性不变量 / 客户端限流稳态 / Envoy 梯度 / Netflix Vegas。"""

import math
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from overload import (  # noqa: E402
    CRITICAL,
    CRITICALITY_ORDER,
    CRITICAL_PLUS,
    SHEDDABLE,
    SHEDDABLE_PLUS,
    CriticalityShedder,
    backend_reject_ratio,
    client_throttle_equilibrium,
    criticality_rank,
    envoy_gradient,
    envoy_headroom,
    envoy_update,
)
from vegas import VegasLimit, log10_root  # noqa: E402

FAILED = []
N = 0


def ck(cond, msg):
    global N
    N += 1
    if not cond:
        FAILED.append(msg)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def fixed(v):
    """确定性抖动源。"""
    return lambda: v


# ---------------------------------------------------- 1. 关键性分级（SRE 书）
ck(len(CRITICALITY_ORDER) == 4, "恰好四个关键性值")
ck(criticality_rank(SHEDDABLE) == 0, "SHEDDABLE 最低")
ck(criticality_rank(CRITICAL_PLUS) == 3, "CRITICAL_PLUS 最高")
ck(criticality_rank(CRITICAL) > criticality_rank(SHEDDABLE_PLUS), "CRITICAL 高于 SHEDDABLE_PLUS")
try:
    criticality_rank("NO_SUCH")
    ck(False, "未知关键性应报错")
except ValueError:
    ck(True, "未知关键性报 ValueError")

shed = CriticalityShedder({SHEDDABLE: 0.6, SHEDDABLE_PLUS: 0.7,
                           CRITICAL: 0.85, CRITICAL_PLUS: 0.95})
# 阈值必须随关键性严格升高，否则构造失败
for bad in ({SHEDDABLE: 0.9, SHEDDABLE_PLUS: 0.7, CRITICAL: 0.85, CRITICAL_PLUS: 0.95},
            {SHEDDABLE: 0.6, SHEDDABLE_PLUS: 0.7}):
    try:
        CriticalityShedder(bad)
        ck(False, "非法阈值应报错")
    except ValueError:
        ck(True, "非法阈值报 ValueError")

ck(shed.serves(CRITICAL_PLUS, 0.5), "低利用率下全部服务")
ck(not shed.serves(SHEDDABLE, 0.65), "0.65 时 SHEDDABLE 已被拒")
ck(shed.serves(CRITICAL, 0.65), "0.65 时 CRITICAL 仍被服务")
ck(shed.rejected_set(0.65) == [SHEDDABLE], "0.65 只拒 SHEDDABLE")
ck(shed.rejected_set(0.99) == list(CRITICALITY_ORDER), "0.99 全部被拒")

# SRE 书的不变量：拒绝某关键性 => 所有更低的都已被拒
for u in (0.0, 0.5, 0.61, 0.69, 0.72, 0.86, 0.94, 0.99):
    ck(shed.check_invariant(u), f"利用率 {u} 满足关键性单调不变量")

# 反过来：被拒集合必须是「从最低开始的一段前缀」
for u in (0.61, 0.75, 0.9, 0.99):
    rej = shed.rejected_set(u)
    ck(rej == list(CRITICALITY_ORDER)[:len(rej)], f"利用率 {u} 被拒集合是前缀")

# ------------------------------------------- 2. 客户端自适应限流（SRE 书）
# 未过载：零拒绝
s, a, br, lr = client_throttle_equilibrium(100, 200)
ck((s, a, br, lr) == (100, 100, 0, 0), "G <= C 时零拒绝")
# 后端过载但客户端尚未自限：C < G <= K*C
s, a, br, lr = client_throttle_equilibrium(300, 200)
ck((s, a, br, lr) == (300, 200, 100, 0), "C < G <= K*C 时后端拒 100、本地拒 0")
# 客户端自限：G > K*C
s, a, br, lr = client_throttle_equilibrium(1000, 200, K=2.0)
ck((s, a, br, lr) == (400, 200, 200, 600), "G=1000,C=200,K=2 -> 发 400 接 200 后端拒 200 本地拒 600")
ck(close(br / a, 1.0), "K=2 时后端「拒:接」= 1:1（原书结论）")
ck(close(backend_reject_ratio(2.0), 1.0), "K=2 -> 每处理 1 个拒 1 个")
ck(close(backend_reject_ratio(1.1), 0.1), "K=1.1 -> 每接受 10 个拒 1 个（原书结论）")
s2, a2, br2, lr2 = client_throttle_equilibrium(1000, 200, K=1.1)
# 1.1*200 在 IEEE754 下是 220.00000000000003，用容差而不是 ==
ck(close(s2, 220.0) and a2 == 200 and close(br2, 20.0) and close(lr2, 780.0),
   f"K=1.1 -> 发 {s2} 接 {a2} 后端拒 {br2} 本地拒 {lr2}")
ck(close(br2 / a2, 0.1), "K=1.1 时后端「拒:接」= 1:10")
# 后端接受量永不超出其能力
for G in (10, 100, 500, 5000, 50000):
    for K in (1.1, 1.5, 2.0, 3.0):
        _, a3, _, _ = client_throttle_equilibrium(G, 200, K)
        ck(a3 <= 200, f"G={G},K={K} 后端接受量不超过 C")
        ck(a3 >= 0, "接受量非负")
# K 越大，后端负载越重（原书：K 小 = 更激进）
for K in (1.1, 1.5, 2.0, 3.0):
    sK, _, brK, _ = client_throttle_equilibrium(10000, 200, K)
    ck(close(sK, K * 200), f"K={K} 时发送速率钉在 K*C")
    ck(close(brK, (K - 1) * 200), f"K={K} 时后端拒绝 (K-1)*C")
try:
    client_throttle_equilibrium(-1, 200)
    ck(False, "负到达率应报错")
except ValueError:
    ck(True, "负到达率报 ValueError")
try:
    client_throttle_equilibrium(100, 200, K=0)
    ck(False, "K=0 应报错")
except ValueError:
    ck(True, "K=0 报 ValueError")

# ------------------------------------------------- 3. Envoy 梯度控制器
ck(close(envoy_gradient(10, 10), 1.0), "sampleRTT == minRTT 且无 buffer -> 1.0")
ck(close(envoy_gradient(10, 20), 0.5), "sampleRTT 翻倍 -> 0.5")
ck(close(envoy_gradient(10, 20, buffer_pct=0.1), 0.55), "buffer 10% -> 0.55")
# 成对构造：5% 的抖动在有无 buffer 时方向相反
ck(envoy_gradient(10, 10.5) < 1.0, "无 buffer 时 5% 抖动 -> 缩减")
ck(envoy_gradient(10, 10.5, buffer_pct=0.1) > 1.0, "10% buffer 时 5% 抖动 -> 仍扩张")
ck(close(envoy_gradient(10, 10.5, buffer_pct=0.1), 1.1 / 1.05), "buffer 口径 = (minRTT+B)/sampleRTT")
# 梯度随 sampleRTT 单调下降
prev = 2.0
for r in (10, 12, 15, 20, 40, 100):
    g = envoy_gradient(10, r)
    ck(g < prev, f"gradient 随 sampleRTT 递减（{r}）")
    prev = g
# headroom 是 sqrt(limit) 且不可配置
for lim in (1, 4, 25, 100, 1000):
    ck(close(envoy_headroom(lim), math.sqrt(lim)), f"headroom({lim}) = sqrt")
# limit_new = gradient*limit + headroom
ck(close(envoy_update(100, 1.0), 110.0), "gradient=1 时 limit 增长 sqrt(100)=10")
ck(close(envoy_update(100, 0.5), 60.0), "gradient=0.5 时 50 + 10")
# 有 headroom 才能从停滞中爬出来（原书的理由）
ck(envoy_update(1, 1.0) > 1.0, "headroom 让小 limit 也能增长")
# 夹逼
ck(envoy_update(100, 0.01, min_concurrency_limit=25) == 25, "不低于 min_concurrency_limit")
ck(envoy_update(100, 100.0, max_concurrency_limit=500) == 500, "不超过 max_concurrency_limit")
ck(envoy_update(100, 1.0, min_concurrency_limit=25, max_concurrency_limit=500) == 110.0,
   "区间内不夹逼")
try:
    envoy_gradient(0, 10)
    ck(False, "minRTT=0 应报错")
except ValueError:
    ck(True, "minRTT=0 报 ValueError")

# -------------------------------------------------- 4. Netflix VegasLimit
# LOG10 查表（Java: max(1, (int)log10(t))）
for t, want in ((1, 1), (9, 1), (10, 1), (99, 1), (100, 2), (999, 2), (1000, 3)):
    ck(log10_root(t) == want, f"LOG10({t}) = {want}")
# t <= 1 时 Java 走 max(1, (int)log10(0/1))，结果为 1
ck(log10_root(0) == 1, "LOG10(0) = 1（Java 的 -Inf 转 int 后取 max）")

v = VegasLimit(initial_limit=100, max_concurrency=1000, jitter_source=fixed(0.5))
# LOG10(100) = (int)log10(100) = 2，所以 alpha/beta/threshold = 6/12/2
ck(v.alpha() == 6, f"limit=100 -> alpha = 3*LOG10(100) = 6（实际 {v.alpha()}）")
ck(v.beta() == 12, f"limit=100 -> beta = 6*LOG10(100) = 12（实际 {v.beta()}）")
ck(v.threshold() == 2, f"limit=100 -> threshold = LOG10(100) = 2（实际 {v.threshold()}）")
ck(close(v.increase(100), 102), "increase = limit + LOG10(limit) = 102")
ck(close(v.decrease(100), 98), "decrease = limit - LOG10(limit) = 98")
# limit=20 -> LOG10=1 -> alpha=3, beta=6, threshold=1
v20 = VegasLimit(initial_limit=20, jitter_source=fixed(0.5))
ck((v20.alpha(), v20.beta(), v20.threshold()) == (3, 6, 1), "limit=20 -> 3/6/1")
# javadoc 与实现的差异：limit=1000 时 javadoc 口径是 100，实现是 9
ck(VegasLimit(initial_limit=1000, jitter_source=fixed(0.5)).alpha() == 9,
   "limit=1000 -> 实现给 alpha=9（javadoc 的 max(3,10%) 是 100，以源码为准）")

# queueSize 公式
ck(VegasLimit.queue_size(100, 10, 20) == 50, "queueSize = ceil(100*(1-10/20)) = 50")
ck(VegasLimit.queue_size(100, 10, 10) == 0, "无排队时 queueSize = 0")
# 浮点灰尘：数学上是 20，但 1 − 10/30 在 IEEE754 下是 0.6666666666666667，
# ×30 得 20.000000000000004，ceil 之后是 21。Java 源码同一套算术，结果相同。
ck(VegasLimit.queue_size(30, 10, 30) == 21,
   "queueSize(30,10,30) 实测 21（数学期望 20，浮点灰尘上取整）")
# 15/16 这类二进制精确的比值没有灰尘
ck(VegasLimit.queue_size(32, 15, 16) == 2, "queueSize(32,15,16) = 2（0.0625 精确）")
ck(VegasLimit.queue_size(320, 15, 16) == 20, "queueSize(320,15,16) = 20（精确）")
try:
    VegasLimit.queue_size(100, 10, 0)
    ck(False, "rtt=0 应报错")
except ValueError:
    ck(True, "queueSize 的 rtt=0 报 ValueError")

# 首次样本建立 noload 基线，且不改变 limit
v1 = VegasLimit(initial_limit=100, jitter_source=fixed(0.5))
ck(v1.update(rtt=10, inflight=100) == 100, "首样本只建基线")
ck(v1.rtt_noload == 10, "rtt_noload = 10")
# 更低的 RTT 刷新基线并直接返回
ck(v1.update(rtt=8, inflight=100) == 100, "更低 RTT 刷新基线不改 limit")
ck(v1.rtt_noload == 8, "rtt_noload 降到 8")

# 四个分支。为了让 queueSize 落在边界上又不被浮点灰尘带偏，
# 统一用 nl/rtt = 15/16（二进制精确）这类比值：queueSize = limit/16。
def staged(limit, rtt, nl=15):
    """建一个已建立 noload 基线的 VegasLimit。"""
    v = VegasLimit(initial_limit=limit, jitter_source=fixed(0.5))
    v.update(rtt=nl, inflight=limit)
    v.rtt_noload = nl
    return v.update(rtt=rtt, inflight=limit)

# q = 0 <= threshold(1) -> +beta(6)      [limit=32, LOG10=1]
ck(staged(32, 16, nl=16) == 38, "queueSize=0 <= threshold -> +beta = 38")
# q == threshold(1) 仍走 +beta           [limit=32, nl/rtt = 31/32 -> q=1]
ck(staged(32, 32, nl=31) == 38, "queueSize == threshold -> +beta = 38（边界含等号）")
# threshold(1) < q=2 < alpha(3) -> +LOG10(1)   [limit=32, nl/rtt=15/16 -> q=2]
ck(staged(32, 16) == 33, "queueSize<alpha -> +LOG10 = 33")
# alpha(3) <= q=6 <= beta(6) -> 原样     [limit=96, q = 96/16 = 6]
ck(staged(96, 16) == 96, "queueSize == beta 时保持不变（甜蜜区上界含等号）")
# q=20 > beta(12) -> -LOG10(2)          [limit=320, q = 320/16 = 20, LOG10=2]
ck(staged(320, 16) == 318, "queueSize>beta -> -LOG10 = 318")

# didDrop 优先于一切分支
v6 = VegasLimit(initial_limit=100, jitter_source=fixed(0.5))
v6.update(rtt=10, inflight=100)
ck(v6.update(rtt=10, inflight=100, did_drop=True) == 98, "didDrop -> decrease = 98")

# inflight 不足一半时原样返回（防向上漂移）——与被限流的情况成对
v7 = VegasLimit(initial_limit=100, jitter_source=fixed(0.5))
v7.update(rtt=10, inflight=100)
ck(v7.update(rtt=10, inflight=100) == 112, "inflight 贴近上限时扩张（queueSize=0 -> +beta=12）")
v8 = VegasLimit(initial_limit=100, jitter_source=fixed(0.5))
v8.update(rtt=10, inflight=100)
ck(v8.update(rtt=100, inflight=10) == 100, "inflight*2 < limit 时原样返回")
ck(v8.update(rtt=100, inflight=60) == 98, "同样 RTT，inflight 足够时才缩减")

# 上限夹逼与 smoothing
v9 = VegasLimit(initial_limit=1000, max_concurrency=1000, jitter_source=fixed(0.5))
v9.update(rtt=10, inflight=1000)
ck(v9.update(rtt=10, inflight=1000) <= 1000, "不超过 maxConcurrency")
vs = VegasLimit(initial_limit=100, jitter_source=fixed(0.5), smoothing=0.5)
vs.update(rtt=10, inflight=100)
# 100 -> new=112（+beta 12），smoothing=0.5 -> 0.5*100 + 0.5*112 = 106
ck(vs.update(rtt=10, inflight=100) == 106, "smoothing=0.5 时只走一半")

# probe：jitter=0.5, multiplier=30, limit=100 -> 1500 次后探针
vp = VegasLimit(initial_limit=100, jitter_source=fixed(0.5))
vp.update(rtt=10, inflight=100)
ck(not vp.should_probe(), "刚重置后不探探针")
vp.probe_count = 1499
ck(not vp.should_probe(), "1499 < 1500 不探探针")
vp.probe_count = 1500
ck(vp.should_probe(), "1500 时触发探针")
ck(vp.update(rtt=50, inflight=100) == 100, "探针样本不改 limit")
ck(vp.rtt_noload == 50, "探针刷新 rtt_noload")
ck(vp.probe_count == 0, "探针后计数归零")

try:
    VegasLimit(initial_limit=100, jitter_source=fixed(0.5)).update(rtt=0, inflight=1)
    ck(False, "rtt=0 应报错")
except ValueError:
    ck(True, "rtt=0 报 ValueError")
try:
    VegasLimit(initial_limit=100, jitter_source=None)
    ck(False, "无抖动源应报错")
except ValueError:
    ck(True, "未注入抖动源报 ValueError")

print(f"assertions: {N}, failed: {len(FAILED)}")
for f in FAILED:
    print("FAILED:", f)
sys.exit(1 if FAILED else 0)
