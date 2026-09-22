#!/usr/bin/env python3
"""``id_batcher.go`` 的逐行转写（OpenTelemetry Collector Contrib tail sampling 的批管道）。

实读源码 ``processor/tailsamplingprocessor/internal/idbatcher/id_batcher.go``：

1. 批 id 是**单调递增的绝对编号**，不是下标。``AddToCurrentBatch`` 返回
   ``takeID + len(batches)``——也就是"当前正在建的那批"的 id，它永远比
   即将被取出的批（``takeID``）大 ``numBatches``；
2. ``CloseCurrentAndTakeFirstBatch`` 取 ``batches[takeID % numBatches]``，
   把 currentBatch 补进这个槽位再把 takeID +1。于是**一个元素在进了 currentBatch 之后，
   要等 numBatches 次 Close 才会被取出**；
3. ``MoveToEarlierBatch(id, traceCurrentBatch, batchesFromNow)`` 里
   ``proposed = takeID + batchesFromNow``，**只有 proposed < traceCurrentBatch 才搬**。
   因此「加速」在区间内才有效：若 ``batchesFromNow >= numBatches``，proposed 不会更小，
   搬移是空操作；
4. 搬移的落点是 ``proposed % numBatches``，而该槽位此刻装的是**编号等于 proposed 的那批**——
   ``batches`` 数组是环形复用，没有冲突正是因为取出的批总是比写入的批小 numBatches；
5. ``Stop`` 之后不再补充新批，管道被读空即止（``lastBatchID = takeID + numBatches``）。
"""

from __future__ import annotations


class Batcher:
    def __init__(self, num_batches: int, new_batches_capacity: int = 10) -> None:
        if num_batches < 1:
            raise ValueError("invalid number of batches, it must be greater than zero")
        if new_batches_capacity == 0:
            new_batches_capacity = 10
        self.num_batches = num_batches
        self.capacity = new_batches_capacity
        # 管道里 numBatches 个槽位，初始都是空批
        self.batches: list[set[str]] = [set() for _ in range(num_batches)]
        self.current: set[str] = set()
        self.take_id = 0
        # Go 版是 math.MaxUint64：Stop 之前条件恒真，管道永远处在"有批可读"的状态
        self.last_batch_id = (1 << 64) - 1
        self.stopped = False

    # -- 写入 --
    def add_to_current_batch(self, trace_id: str) -> int:
        self.current.add(trace_id)
        return self.take_id + self.num_batches

    def move_to_earlier_batch(self, trace_id: str, trace_current_batch: int, batches_from_now: int) -> int:
        proposed = self.take_id + batches_from_now
        if proposed >= trace_current_batch:
            return trace_current_batch          # 只会往前搬，"往后搬"是空操作

        current_batch_id = self.take_id + self.num_batches
        if trace_current_batch == current_batch_id:
            self.current.discard(trace_id)
        else:
            idx = trace_current_batch % self.num_batches
            self.batches[idx].discard(trace_id)

        self.batches[proposed % self.num_batches].add(trace_id)
        return proposed

    def remove_from_batch(self, trace_id: str, batch: int) -> None:
        current_batch_id = self.take_id + self.num_batches
        if batch == current_batch_id:
            self.current.discard(trace_id)
        elif self.take_id <= batch < current_batch_id:
            self.batches[batch % self.num_batches].discard(trace_id)
        # 越界即 noop

    # -- 取出 --
    def close_current_and_take_first_batch(self) -> tuple[set[str], bool]:
        if self.take_id >= self.last_batch_id:
            read = self.current
            self.current = set()
            return read, False
        idx = self.take_id % self.num_batches
        read = self.batches[idx]
        if not self.stopped:
            self.batches[idx] = self.current
            self.current = set()
        self.take_id += 1
        return read, True

    def stop(self) -> None:
        self.stopped = True
        self.last_batch_id = self.take_id + self.num_batches


def num_decision_batches(decision_wait_seconds: float) -> int:
    """``numDecisionBatches := math.Max(1, tsp.cfg.DecisionWait.Seconds())``。"""
    return max(1, int(decision_wait_seconds))
