#!/usr/bin/env python3
"""把尾采样的四个「时间/空间」行为跑出来。运行：``python main.py``"""

from idbatcher import Batcher, num_decision_batches
from processor import (
    NOT_SAMPLED, SAMPLED, UNSPECIFIED, DecisionCache, TailSampler, combine, num_drop_policies,
)


def demo_decision_latency():
    print("== 1. 决策延迟 == decision_wait（批数 = 秒数）==")
    for wait in (30, 5, -1):
        n = num_decision_batches(wait)
        b = Batcher(n)
        bid = b.add_to_current_batch("T")
        ticks = 0
        while "T" not in b.close_current_and_take_first_batch()[0]:
            ticks += 1
            if ticks > 200:
                break
        print(f"  decision_wait={wait:>3}s -> numDecisionBatches={n}, 首次入批 id={bid}, "
              f"实际 {ticks + 1} 次 tick 后被决策")


def demo_root_acceleration():
    print("\n== 2. 根 span 加速：只有 R < decision_wait 才生效 ==")
    for wait, root_wait in ((30, 5), (5, 30), (10, 10)):
        b = Batcher(num_decision_batches(wait))
        bid = b.add_to_current_batch("T")
        new_bid = b.move_to_earlier_batch("T", bid, root_wait)
        moved = new_bid != bid
        delay = (new_bid - b.take_id) if moved else wait
        print(f"  decision_wait={wait}s  after_root={root_wait}s -> 搬移={moved!s:5} "
              f"实际决策延迟≈{delay}s")


def demo_eviction():
    print("\n== 3. num_traces 满：非阻塞模式淘汰最老待决 trace（且不给决策）==")
    s = TailSampler(decision_wait=5, num_traces=3)
    for i in range(5):
        s.add_span(f"T{i}")
    print(f"  内存中 trace: {sorted(s.id_to_trace)}   被淘汰: {s.evicted}")
    print(f"  淘汰的 {s.evicted} 从未出现在 emitted 里: "
          f"{all(t not in [e for e, _ in s.emitted] for t in s.evicted)}")
    s.tick()
    s.tick()
    s.tick()
    s.tick()
    s.tick()
    s.tick()
    print(f"  6 次 tick 后 emitted={s.emitted}  id_not_found={s.id_not_found}")


def demo_no_turning_back():
    print("\n== 4. 决策不回头 + 决策缓存兜住晚到 span ==")
    cache = DecisionCache(sampled_size=8, non_sampled_size=8)
    s = TailSampler(decision_wait=3, num_traces=10, cache=cache)
    s.add_span("T")
    for _ in range(4):                      # 3 个批 + 1 次 Close 才轮到它
        s.tick(decide=lambda t, m: SAMPLED)
    before = len(s.emitted)
    print(f"  4 次 tick 后 emitted={s.emitted}")
    s.tick()
    print(f"  已决策后再 tick: emitted 数量 {before} -> {len(s.emitted)}（不变）")
    s.add_span("T")                          # 晚到 span
    print(f"  晚到 span 走缓存: emitted 追加 {s.emitted[-1]}，未重建 trace: {'T' not in s.id_to_trace}")


def demo_policy_combination():
    print("\n== 5. 策略组合：drop 是前缀，sample 优先 ==")
    print(f"  [drop, sample]                 -> {combine(['drop', 'sample'])}")
    print(f"  [sample, not_sample]           -> {combine(['sample', 'not_sample'])}")
    print(f"  [inverted_sample]              -> {combine(['inverted_sample'])}")
    print(f"  [inverted_sample, not_sample]  -> {combine(['inverted_sample', 'not_sample'])}")
    print(f"  [inverted_not_sample, sample]  -> {combine(['inverted_not_sample', 'sample'])}")
    print(f"  []                             -> {combine([])}")
    policies = [{"is_drop": True}, {"is_drop": True}, {"is_drop": False}, {"is_drop": True}]
    print(f"  drop 前缀长度: {num_drop_policies(policies)}（第 4 个 is_drop=True 但不在前缀里）")


if __name__ == "__main__":
    demo_decision_latency()
    demo_root_acceleration()
    demo_eviction()
    demo_no_turning_back()
    demo_policy_combination()
    print(f"\n常量: UNSPECIFIED={UNSPECIFIED} SAMPLED={SAMPLED} NOT_SAMPLED={NOT_SAMPLED}")
