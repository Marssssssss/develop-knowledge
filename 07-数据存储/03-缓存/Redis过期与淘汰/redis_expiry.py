"""Redis 键过期与淘汰策略 —— 语义与参数模型。

权威依据（本轮实际抓取/阅读）：
  - redis.io《Key eviction》：策略清单（含 Redis 8 新增的 allkeys-lrm / volatile-lrm）、
    volatile-xxx 在无 TTL 键时退化为 noeviction、maxmemory-samples 默认 5、
    3.0 起的候选池、LFU 的 Morris 计数器与 lfu-log-factor/lfu-decay-time 表格、
    mem_not_counted_for_evict 与「淘汰 → 产生 DEL → 缓冲区变大 → 再淘汰」的反馈环。
  - redis.io《EXPIRE》：NX/XX/GT/LT（7.0 加入）、non-volatile 视为 infinite TTL、
    哪些命令清 TTL 哪些不清、非正数/过去时间触发的是 del 而非 expired、
    副本不独立过期而等主的 DEL。
  - redis/src/expire.c：ACTIVE_EXPIRE_CYCLE_* 五个常量与 effort 换算公式。
  - redis/src/evict.c：LFULogIncr / LFUDecrAndReturn / LFUTimeElapsed 源码。
  - redis/src/server.h：LFU_INIT_VAL 5。
"""

# ---------------------------------------------------------------- EXPIRE

CLEAR_TTL_COMMANDS = {"DEL", "SET", "GETSET"}   # 还有所有 *STORE
KEEP_TTL_COMMANDS = {"INCR", "LPUSH", "HSET", "APPEND", "SETEX"}


def command_clears_ttl(cmd):
    """redis.io EXPIRE 页：只有「删除或整体覆盖内容」的命令才清 TTL。"""
    cmd = cmd.upper()
    if cmd in CLEAR_TTL_COMMANDS or cmd.endswith("STORE"):
        return True
    if cmd in KEEP_TTL_COMMANDS:
        return False
    raise ValueError("unknown command: %s" % cmd)


INF = float("inf")


def expire(key_state, ttl, option=None, now=0):
    """模拟 EXPIRE key seconds [NX|XX|GT|LT]。

    key_state: None 表示键不存在；否则 {"ttl": int|None, "expire_at": int|None}
    返回 (是否设置成功, 新状态)。非易失键在 GT/LT 比较中视为 infinite TTL。
    """
    if key_state is None:
        return 0, None
    cur = key_state.get("expire_at")
    # 官方文档比的是**过期时间点**（绝对值），不是剩余 TTL。
    # 非易失键的「当前过期时间」视为无穷大。
    cur_expire_at = INF if cur is None else cur
    new_expire_at = now + ttl
    if option == "NX":
        ok = cur is None
    elif option == "XX":
        ok = cur is not None
    elif option == "GT":
        ok = new_expire_at > cur_expire_at  # 非易失 = infinite，故永不成立
    elif option == "LT":
        ok = new_expire_at < cur_expire_at  # 非易失 = infinite，故恒成立
    elif option is None:
        ok = True
    else:
        raise ValueError("unknown option: %s" % option)
    if not ok:
        return 0, key_state
    return 1, {"ttl": ttl, "expire_at": now + ttl}


def expire_event(ttl=None, at_time_in_past=False):
    """非正数超时 / 过去时间 → 键被 **删除**，键事件是 del 而不是 expired。"""
    if (ttl is not None and ttl <= 0) or at_time_in_past:
        return "del"
    return "expired"


# ---------------------------------------------------------------- 定期删除

# redis/src/expire.c
ACTIVE_EXPIRE_CYCLE_KEYS_PER_LOOP = 20
ACTIVE_EXPIRE_CYCLE_FAST_DURATION = 1000     # 微秒
ACTIVE_EXPIRE_CYCLE_SLOW_TIME_PERC = 25
ACTIVE_EXPIRE_CYCLE_ACCEPTABLE_STALE = 10
DEFAULT_HZ = 10                              # 源码注释："usually 10 hertz"


def active_expire_cycle_params(active_expire_effort):
    """expire.c 里的 effort 换算（effort = server.active_expire_effort - 1）。

    active_expire_effort 取值 1..10，默认 1 → effort = 0。
    """
    effort = active_expire_effort - 1        # Rescale from 0 to 9
    return {
        "keys_per_loop": ACTIVE_EXPIRE_CYCLE_KEYS_PER_LOOP
                         + ACTIVE_EXPIRE_CYCLE_KEYS_PER_LOOP // 4 * effort,
        "fast_duration_us": ACTIVE_EXPIRE_CYCLE_FAST_DURATION
                            + ACTIVE_EXPIRE_CYCLE_FAST_DURATION // 4 * effort,
        "slow_time_perc": ACTIVE_EXPIRE_CYCLE_SLOW_TIME_PERC + 2 * effort,
        "acceptable_stale": ACTIVE_EXPIRE_CYCLE_ACCEPTABLE_STALE - effort,
    }


# ---------------------------------------------------------------- LFU

LFU_INIT_VAL = 5        # server.h
LFU_COUNTER_MAX = 255
LFU_MINUTES_BITS = 16
LFU_MINUTES_WRAP = (1 << LFU_MINUTES_BITS) - 1     # 65535


def lfu_init_lru(minutes):
    """object.c: o->lru = (LFUGetTimeInMinutes() << 8) | LFU_INIT_VAL"""
    return ((minutes & LFU_MINUTES_WRAP) << 8) | LFU_INIT_VAL


def lfu_unpack(lru):
    """evict.c: ldt = lru >> 8; counter = lru & 255"""
    return lru >> 8, lru & 255


def lfu_time_elapsed(ldt, now_minutes):
    """evict.c：16 位分钟时间只环绕一次，所以要特殊处理回绕。"""
    now = now_minutes & LFU_MINUTES_WRAP
    if now >= ldt:
        return now - ldt
    return LFU_MINUTES_WRAP - ldt + now


def lfu_log_incr(counter, r, lfu_log_factor=10):
    """evict.c LFULogIncr：Morris 概率计数器。

    p = 1 / ((counter - LFU_INIT_VAL) * lfu_log_factor + 1)
    """
    if counter == LFU_COUNTER_MAX:
        return LFU_COUNTER_MAX
    baseval = counter - LFU_INIT_VAL
    if baseval < 0:
        baseval = 0
    p = 1.0 / (baseval * lfu_log_factor + 1)
    return counter + 1 if r < p else counter


def lfu_decay(counter, ldt, now_minutes, lfu_decay_time=1):
    """evict.c LFUDecrAndReturn：按经过的「衰减周期数」线性扣减，下限 0。

    lfu_decay_time = 0 表示**永不衰减**。
    """
    if lfu_decay_time == 0:
        return counter
    num_periods = lfu_time_elapsed(ldt, now_minutes) // lfu_decay_time
    if num_periods:
        counter = 0 if num_periods > counter else counter - num_periods
    return counter


def lfu_idle(counter):
    """evict.c：idle = 255 - LFUDecrAndReturn(kv)，选 idle 最大者淘汰。"""
    return 255 - counter


def lfu_expected_counter_exact(hits, lfu_log_factor=10, init=LFU_INIT_VAL):
    """精确马尔可夫链：在 0..255 的计数器上跑概率分布，返回期望值。

    只对小 hits 使用（O(hits × 256)）；本函数用于校验上面的均值场近似。
    """
    dist = {init: 1.0}
    for _ in range(hits):
        nxt = {}
        for c, p in dist.items():
            if p == 0.0:
                continue
            if c >= LFU_COUNTER_MAX:
                nxt[LFU_COUNTER_MAX] = nxt.get(LFU_COUNTER_MAX, 0.0) + p
                continue
            baseval = max(0, c - LFU_INIT_VAL)
            pr = 1.0 / (baseval * lfu_log_factor + 1)
            nxt[c + 1] = nxt.get(c + 1, 0.0) + p * pr
            nxt[c] = nxt.get(c, 0.0) + p * (1.0 - pr)
        dist = nxt
    return sum(c * p for c, p in dist.items())


def lfu_expected_counter(hits, lfu_log_factor=10, init=LFU_INIT_VAL):
    """均值场近似：c_{n+1} = c_n + 1/((c_n - 5) * f + 1)。

    真实过程是随机的，官方表格给的是一次参考运行的结果，
    本函数用于**复现表格的量级**（误差见自检的容差断言）。
    """
    c = float(init)
    for _ in range(hits):
        if c >= LFU_COUNTER_MAX:
            return LFU_COUNTER_MAX
        baseval = max(0.0, c - LFU_INIT_VAL)
        c += 1.0 / (baseval * lfu_log_factor + 1)
    return min(LFU_COUNTER_MAX, c)


# redis.io eviction 文档给出的官方表格
OFFICIAL_LFU_TABLE = {
    0:   {100: 104, 1000: 255, 100000: 255, 1000000: 255},
    1:   {100: 18, 1000: 49, 100000: 255, 1000000: 255},
    10:  {100: 10, 1000: 18, 100000: 142, 1000000: 255},
    100: {100: 8, 1000: 11, 100000: 49, 1000000: 143},
}

# ---------------------------------------------------------------- 策略

POLICIES = [
    "noeviction", "allkeys-lru", "allkeys-lrm", "allkeys-lfu", "allkeys-random",
    "volatile-lru", "volatile-lrm", "volatile-lfu", "volatile-random",
    "volatile-ttl",
]
VOLATILE_POLICIES = {p for p in POLICIES if p.startswith("volatile-")}


def eviction_result(policy, has_volatile_keys):
    """volatile-xxx 在没有可淘汰键（无 TTL）时**表现得就像 noeviction**。"""
    if policy == "noeviction":
        return "error"
    if policy in VOLATILE_POLICIES and not has_volatile_keys:
        return "error"      # 官方文档原话："behave like noeviction"
    return "evict"


def counted_for_evict(used, aof_buffer, repl_buffer):
    """evict.c freeMemoryGetNotCountedMemory：AOF/复制缓冲区**不计入** maxmemory。

    否则「淘汰 → 产生 DEL → 缓冲区变大 → 触发更多淘汰」会形成正反馈。
    """
    return used
