#!/usr/bin/env python3
"""demo 597 尾采样自检。断言全部对应实读的 Go 源码行为，实跑验证。"""

import sys

from idbatcher import Batcher, num_decision_batches
from processor import (
    NOT_SAMPLED, SAMPLED, UNSPECIFIED, DecisionCache, TailSampler, combine, num_drop_policies,
)

FAILED: list[str] = []
COUNT = 0


def check(name, cond):
    global COUNT
    COUNT += 1
    if not cond:
        FAILED.append(name)
        print(f"  FAIL {name}")


def eq(name, got, want):
    global COUNT
    COUNT += 1
    if got != want:
        FAILED.append(f"{name} (got={got!r} want={want!r})")
        print(f"  FAIL {name}: got={got!r} want={want!r}")


# --- 1. idbatcher：批 id 是绝对编号 ---
b = Batcher(5)
eq("numBatches=5 时入批 id = takeID+5", b.add_to_current_batch("A"), 5)
eq("第二个 id 同批", b.add_to_current_batch("B"), 5)
eq("numDecisionBatches = max(1, seconds)", num_decision_batches(30), 30)
eq("numDecisionBatches 下限为 1", num_decision_batches(0), 1)
eq("numDecisionBatches 负数为 1", num_decision_batches(-1), 1)

# --- 2. 决策延迟 = numBatches + 1 次 Close ---
for wait in (1, 3, 7):
    bb = Batcher(num_decision_batches(wait))
    bb.add_to_current_batch("T")
    ticks = 0
    while "T" not in bb.close_current_and_take_first_batch()[0]:
        ticks += 1
        check("tick 循环有界", ticks < 100)
    eq(f"decision_wait={wait} → 第 {wait+1} 次 Close 才轮到", ticks + 1, wait + 1)

# --- 3. MoveToEarlierBatch 只在往前搬时生效 ---
b = Batcher(30)
bid = b.add_to_current_batch("T")
eq("往前搬返回新批号", b.move_to_earlier_batch("T", bid, 5), 5)
check("往前搬后 id 离开原批", "T" not in b.batches[bid % 30])
check("往前搬后 id 落在目标槽", "T" in b.batches[5 % 30])
b2 = Batcher(5)
bid2 = b2.add_to_current_batch("T")
eq("batchesFromNow >= numBatches 时空操作", b2.move_to_earlier_batch("T", bid2, 5), bid2)
b3 = Batcher(5)
bid3 = b3.add_to_current_batch("T")
eq("相等也是空操作（proposed 不小于 current）", b3.move_to_earlier_batch("T", bid3, 5), bid3)
b4 = Batcher(10)
bid4 = b4.add_to_current_batch("T")
eq("等于 numBatches 时空操作", b4.move_to_earlier_batch("T", bid4, 10), bid4)

# --- 4. 环形复用不串味：连续 40 个 tick，管道里始终压着 4 批 ---
b = Batcher(4)
seen = []
for t in range(40):
    b.add_to_current_batch(f"T{t}")
    batch, _ = b.close_current_and_take_first_batch()
    seen.extend(sorted(batch))
# 每个 id 要等 4 个批才出来，所以 40 次 Close 只吐出前 36 个
eq("40 次 Close 吐出 36 个（管道里还压 4 批）", len(seen), 36)
eq("无重复", len(seen), len(set(seen)))
check("取出的顺序就是插入顺序", seen == [f"T{t}" for t in range(36)])

# --- 5. 淘汰：队首优先，且不给决策 ---
s = TailSampler(decision_wait=5, num_traces=3)
for i in range(5):
    s.add_span(f"T{i}")
eq("内存只留 3 条", len(s.id_to_trace), 3)
eq("存活的是最后 3 条", sorted(s.id_to_trace), ["T2", "T3", "T4"])
eq("被淘汰的是最老的 2 条", s.evicted, ["T0", "T1"])
for _ in range(6):
    s.tick()
eq("淘汰者从未被采样", [t for t, _ in s.emitted], ["T2", "T3", "T4"])
check("淘汰者会在 tick 里命中 id_not_found", s.id_not_found >= 2)

# block_on_overflow 模式：不淘汰，阻塞等 tick
s = TailSampler(decision_wait=2, num_traces=2, block_on_overflow=True)
s.add_span("A")
s.add_span("B")
s.add_span("C")          # 触发阻塞；tick 会腾出空间
eq("阻塞模式下不淘汰任何 trace", s.evicted, [])
check("A/B 是被正常决策释放的，不是被丢的",
      {t for t, _ in s.emitted} == {"A", "B"} and all(d == SAMPLED for _, d in s.emitted))
eq("腾出空间后 C 才进内存", sorted(s.id_to_trace), ["C"])

# --- 6. 决策不回头 ---
cache = DecisionCache(sampled_size=4, non_sampled_size=4)
s = TailSampler(decision_wait=3, num_traces=10, cache=cache)
s.add_span("T")
for _ in range(4):
    s.tick(decide=lambda t, m: SAMPLED)
eq("决策已产出", len(s.emitted), 1)
s.tick()
s.tick()
eq("后续 tick 不重复决策", len(s.emitted), 1)
s.add_span("T")          # 晚到 span
eq("晚到 span 命中缓存而不是重建 trace", len(s.id_to_trace), 0)
eq("晚到 span 追加一条同结论", s.emitted[-1], ("T", SAMPLED))

# 没开缓存时：晚到 span 会重建 trace，重新走决策窗口
s = TailSampler(decision_wait=3, num_traces=10, cache=DecisionCache())
s.add_span("T")
for _ in range(4):
    s.tick(decide=lambda t, m: SAMPLED)
s.add_span("T")
check("无缓存时晚到 span 重建 trace", "T" in s.id_to_trace)
eq("重建后的决策是 Unspecified", s.id_to_trace["T"]["decision"], UNSPECIFIED)

# --- 7. 决策缓存是 LRU，且两套分开 ---
c = DecisionCache(sampled_size=2, non_sampled_size=1)
c.put("A", SAMPLED)
c.put("B", SAMPLED)
c.put("C", SAMPLED)      # A 被挤掉
eq("LRU 挤掉最早的", c.get("A"), UNSPECIFIED)
eq("B 仍在", c.get("B"), SAMPLED)
c.put("X", NOT_SAMPLED)
c.put("Y", NOT_SAMPLED)  # X 被挤掉
eq("非采样缓存独立", c.get("X"), UNSPECIFIED)
eq("Y 仍在", c.get("Y"), NOT_SAMPLED)
eq("容量为 0 的缓存不生效", DecisionCache().get("A"), UNSPECIFIED)

# --- 8. 策略组合律 ---
eq("drop 压过 sample", combine(["drop", "sample"]), NOT_SAMPLED)
eq("sample 压过 not_sample", combine(["sample", "not_sample"]), SAMPLED)
eq("inverted_not_sample 压过 sample", combine(["inverted_not_sample", "sample"]), NOT_SAMPLED)
eq("单独 inverted_sample → 采样", combine(["inverted_sample"]), SAMPLED)
eq("inverted_sample + not_sample → 不采样",
   combine(["inverted_sample", "not_sample"]), NOT_SAMPLED)
eq("空决策 → 不采样", combine([]), NOT_SAMPLED)
eq("只有 not_sample → 不采样", combine(["not_sample"]), NOT_SAMPLED)

# --- 9. drop 策略只数前缀 ---
eq("全 drop 前缀", num_drop_policies([{"is_drop": True}, {"is_drop": True}]), 2)
eq("遇到非 drop 即停",
   num_drop_policies([{"is_drop": True}, {"is_drop": False}, {"is_drop": True}]), 1)
eq("首条即非 drop → 0", num_drop_policies([{"is_drop": False}, {"is_drop": True}]), 0)
eq("空列表 → 0", num_drop_policies([]), 0)

print(f"\n{COUNT - len(FAILED)}/{COUNT} 断言通过")
sys.exit(1 if FAILED else 0)
