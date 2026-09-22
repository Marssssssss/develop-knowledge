#!/usr/bin/env python3
"""尾采样的状态机：批管道 + ``num_traces`` 淘汰 + 决策缓存 + 决策不可回头。

逐条对应实读的 ``processor/tailsamplingprocessor/processor.go``：

- ``numDecisionBatches = max(1, DecisionWait.Seconds())``：批数 == 决策等待的秒数，
  tick 每秒一次，所以**决策延迟 == decision_wait**（默认 30 s）；
- span 首次到达时 ``AddToCurrentBatch(id)``，同时把 id 压进 ``deleteTraceQueue`` 尾部；
- 根 span 到达且配置了 ``decision_wait_after_root_received`` 时
  ``MoveToEarlierBatch(id, batchID, R)`` —— 把决策提前到 R 秒后；
- ``idToTrace`` 满（``len >= NumTraces``）时：``block_on_overflow=True`` 就阻塞等 tick 腾地方，
  否则**淘汰 ``deleteTraceQueue`` 队首**（最老的待决 trace）且**不给它任何决策**——
  这是尾采样在过载下丢数据的真实形态；
- ``FinalDecision != Unspecified`` 的 trace 在 tick 上被直接跳过："A decision was already
  made, no need to do it again."——**决策不回头**；
- 决策缓存（LRU）只在 trace 已出内存后仍来晚到 span 时兜底；没开缓存的晚到 span
  会重新建一条 trace、重新走一遍决策窗口。
"""

from __future__ import annotations

from collections import OrderedDict

from idbatcher import Batcher, num_decision_batches

UNSPECIFIED, SAMPLED, NOT_SAMPLED = "unspecified", "sampled", "not_sampled"


class DecisionCache:
    """``decision_cache`` 的 LRU 实现：``sampled_cache_size`` / ``non_sampled_cache_size`` 两套。"""

    def __init__(self, sampled_size: int = 0, non_sampled_size: int = 0) -> None:
        self.sampled_size, self.non_sampled_size = sampled_size, non_sampled_size
        self._sampled: OrderedDict[str, None] = OrderedDict()
        self._non: OrderedDict[str, None] = OrderedDict()

    def put(self, trace_id: str, decision: str) -> None:
        if decision == SAMPLED and self.sampled_size > 0:
            self._sampled[trace_id] = None
            self._sampled.move_to_end(trace_id)
            while len(self._sampled) > self.sampled_size:
                self._sampled.popitem(last=False)
        elif decision == NOT_SAMPLED and self.non_sampled_size > 0:
            self._non[trace_id] = None
            self._non.move_to_end(trace_id)
            while len(self._non) > self.non_sampled_size:
                self._non.popitem(last=False)

    def get(self, trace_id: str) -> str:
        if trace_id in self._sampled:
            self._sampled.move_to_end(trace_id)
            return SAMPLED
        if trace_id in self._non:
            self._non.move_to_end(trace_id)
            return NOT_SAMPLED
        return UNSPECIFIED

    def __contains__(self, trace_id: str) -> bool:
        return trace_id in self._sampled or trace_id in self._non


def combine(decisions: list[str]) -> str:
    """§Policy Decision Flow 的组合律。取值对应 Go 的四个常量：
    ``drop`` / ``inverted_not_sample`` / ``sample`` / ``inverted_sample`` /
    ``not_sample``。判定**按序**：

    1. 有 drop → 不采样；
    2. 有 inverted not sample → 不采样；
    3. 有 sample → 采样；
    4. 有 inverted sample **且没有** not sample → 采样；
    5. 其余 → 不采样（含空列表）。

    把 ``sample`` 与 ``inverted_sample`` 当成同一个值会让第 4 条退化成永远采样——
    这正是本 demo 要把两者分开建模的原因。
    """
    ds = set(decisions)
    if "drop" in ds:
        return NOT_SAMPLED
    if "inverted_not_sample" in ds:
        return NOT_SAMPLED
    if "sample" in ds:
        return SAMPLED
    if "inverted_sample" in ds and "not_sample" not in ds:
        return SAMPLED
    return NOT_SAMPLED


def num_drop_policies(policies: list[dict]) -> int:
    """``numDropPolicies`` 只数**前缀**里的 drop 策略，遇到第一个非 drop 就 break。"""
    n = 0
    for p in policies:
        if not p.get("is_drop"):
            break
        n += 1
    return n


class TailSampler:
    def __init__(
        self,
        decision_wait: float = 30.0,
        decision_wait_after_root: float = 0.0,
        num_traces: int = 3,
        block_on_overflow: bool = False,
        cache: DecisionCache | None = None,
    ) -> None:
        self.decision_wait = decision_wait
        self.decision_wait_after_root = decision_wait_after_root
        self.num_traces = num_traces
        self.block_on_overflow = block_on_overflow
        self.cache = cache or DecisionCache()
        self.batcher = Batcher(num_decision_batches(decision_wait))
        self.id_to_trace: dict[str, dict] = {}
        self.queue: list[str] = []
        self.emitted: list[tuple[str, str]] = []
        self.evicted: list[str] = []
        self.id_not_found = 0
        self.ticks = 0

    # -- 数据面 --
    def add_span(self, trace_id: str, is_root: bool = False) -> None:
        if trace_id in self.id_to_trace:
            self.id_to_trace[trace_id]["spans"] += 1
        else:
            cached = self.cache.get(trace_id)
            if cached != UNSPECIFIED:
                # 决策命中缓存：晚到 span 直接按旧结论放行/丢弃，不占内存
                self.emitted.append((trace_id, cached))
                return
            self._ensure_space()
            batch = self.batcher.add_to_current_batch(trace_id)
            self.id_to_trace[trace_id] = {
                "batch": batch, "spans": 1, "decision": UNSPECIFIED, "has_root": False,
            }
            self.queue.append(trace_id)
        if is_root and not self.id_to_trace[trace_id]["has_root"]:
            self.id_to_trace[trace_id]["has_root"] = True
            if self.decision_wait_after_root > 0:
                new_batch = self.batcher.move_to_earlier_batch(
                    trace_id, self.id_to_trace[trace_id]["batch"], int(self.decision_wait_after_root)
                )
                self.id_to_trace[trace_id]["batch"] = new_batch

    def _ensure_space(self) -> None:
        guard = 0
        while len(self.id_to_trace) >= self.num_traces and guard < 10000:
            guard += 1
            if self.block_on_overflow:
                self.tick()
                continue
            front = self.queue[0]
            # 淘汰最老的待决 trace：不给决策，直接丢
            self.evicted.append(front)
            self._drop(front)

    # -- 决策面 --
    def tick(self, decide=lambda trace_id, meta: SAMPLED) -> None:
        self.ticks += 1
        batch, _ = self.batcher.close_current_and_take_first_batch()
        for trace_id in sorted(batch):
            meta = self.id_to_trace.get(trace_id)
            if meta is None:
                self.id_not_found += 1
                continue
            if meta["decision"] != UNSPECIFIED:
                continue          # 决策不回头
            decision = decide(trace_id, meta)
            meta["decision"] = decision
            self.cache.put(trace_id, decision)
            self.emitted.append((trace_id, decision))
            self._drop(trace_id)

    def _drop(self, trace_id: str) -> None:
        """``dropTrace``：只清内存与队列，**不把它从批管道里摘掉**。

        于是这个 id 仍会在之后某个 tick 的批里出现，命中 ``if !ok { idNotFoundOnMapCount++; continue }``
        而被跳过——这就是为什么 ``id_not_found`` 这个指标在淘汰频繁时必然上涨。
        """
        if trace_id in self.id_to_trace:
            del self.id_to_trace[trace_id]
        if trace_id in self.queue:
            self.queue.remove(trace_id)
