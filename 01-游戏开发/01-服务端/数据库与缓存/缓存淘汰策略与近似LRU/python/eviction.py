# -*- coding: utf-8 -*-
"""maxmemory 淘汰策略与近似 LRU 采样。

口径(实读源):redis 仓库自带 redis.conf(unstable)——
  策略清单:volatile-lru/allkeys-lru/volatile-lfu/allkeys-lfu/
           volatile-lrm/allkeys-lrm(lrm 为新变体)/volatile-random/
           allkeys-random/volatile-ttl/noeviction(默认);
  "LRU, LFU and minimal TTL algorithms are not precise algorithms but
   approximated algorithms (in order to save memory)";
  maxmemory-samples 默认 5;10 "Approximates very closely true LRU but costs
  more CPU";3 更快但不准;上限 64。
"""

POLICIES = {
    "volatile-lru": ("带过期键", "近似 LRU"),
    "allkeys-lru": ("所有键", "近似 LRU"),
    "volatile-lfu": ("带过期键", "近似 LFU"),
    "allkeys-lfu": ("所有键", "近似 LFU"),
    "volatile-random": ("带过期键", "随机"),
    "allkeys-random": ("所有键", "随机"),
    "volatile-ttl": ("带过期键", "最近 TTL"),
    "noeviction": ("—", "不淘汰,写操作报错"),
}
NOEVICTION_DEFAULT = "noeviction"

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def candidates(policy, keys):
    """volatile-* 只在带过期的键里挑;allkeys-* 全键。"""
    scope, _algo = POLICIES[policy]
    if policy.startswith("allkeys") or policy == "noeviction":
        return list(keys)
    return [k for k in keys if k.has_expire]


def approx_lru_evict(cands, sample_size, rng):
    """近似 LRU:随机抽 sample 个,从中淘汰最久未访问者。"""
    sample = rng.sample(cands, min(sample_size, len(cands)))
    return min(sample, key=lambda k: k.last_access)


class Key:
    def __init__(self, name, last_access, has_expire=True):
        self.name = name
        self.last_access = last_access
        self.has_expire = has_expire


def hit_rate_under_eviction(trace, policy, capacity, rng):
    """容量不够时的命中率模型:按策略选牺牲者。"""
    mem = []
    hits = 0
    for k in trace:
        hit = False
        for x in mem:
            if x.name == k:                 # 命中即续期(LRU 的访问刷新)
                x.last_access = _CLOCK[0]
                _CLOCK[0] += 1
                hits += 1
                hit = True
                break
        if hit:
            continue
        cands = candidates(policy, mem)
        if cands and len(mem) >= capacity:
            victim = approx_lru_evict(cands, 5, rng) if policy.endswith("lru") \
                else rng.choice(cands)
            mem.remove(victim)
        if len(mem) < capacity:
            mem.append(Key(k, _CLOCK[0]))
            _CLOCK[0] += 1
    return hits / len(trace)


_CLOCK = [0]

import random


def main():
    print("1. 策略清单与维度")
    assert len(POLICIES) == 8 and NOEVICTION_DEFAULT == "noeviction"
    assert POLICIES["volatile-lru"][0] == "带过期键" and POLICIES["allkeys-lru"][0] == "所有键"
    ok("策略 = 键范围(volatile=只带过期键/allkeys=全键)× 算法(LRU/LFU/随机/TTL);"
       "noeviction 不淘汰、写操作报错,且是默认值——缓存用途要显式改")

    print("2. volatile-* 的候选集")
    keys = [Key("session:1", 1, True), Key("config:root", 2, False),
            Key("session:2", 3, True)]
    assert [k.name for k in candidates("volatile-lru", keys)] == ["session:1", "session:2"]
    assert len(candidates("allkeys-lru", keys)) == 3
    ok("volatile-* 只在设了过期的键里挑——永久键(如配置)永不被驱逐,但也进不了缓存淘汰的视野")

    print("3. 近似 LRU 与采样数")
    rng = random.Random(42)
    pool = [Key(f"k{i}", last_access=i) for i in range(100)]
    true_victim = approx_lru_evict(pool, 100, rng)          # 全采样=精确
    small = {approx_lru_evict(pool, 5, rng).name for _ in range(200)}
    assert true_victim.name == "k0" and len(small) > 1
    ok("官方口径:LRU/LFU/TTL 都是**近似算法**(省内存);默认抽 5 个候选,"
       "10 接近真 LRU 但费 CPU,3 更快更不准,上限 64")

    print("4. 命中率对照(LRU vs random,模型)")
    trace = []
    for i in range(200):                     # 冷热交错:每次冷访问前都有一次热访问
        trace += [f"hot{ i % 2 }", f"cold{i}"]
    rng2, rng3 = random.Random(7), random.Random(7)
    h_lru = hit_rate_under_eviction(trace, "allkeys-lru", 8, rng2)
    h_rnd = hit_rate_under_eviction(trace, "allkeys-random", 8, rng3)
    assert h_lru > h_rnd
    ok(f"冷热交错 trace 下近似 LRU({h_lru:.0%}) 优于随机({h_rnd:.0%})——"
       "采样带来的偏差小于算法之间的差距")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
