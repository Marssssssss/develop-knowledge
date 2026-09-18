"""exporter helper 语义:发送队列(背压)、退避重试、持久化。

默认值:
  retry_on_failure  enabled=true initial_interval=5s max_interval=30s
                    max_elapsed_time=300s multiplier=1.5
  sending_queue     enabled=true num_consumers=10 queue_size=5000(sizer=requests)
                    block_on_overflow=false wait_for_result=false
注意 queue_size 存在文档口径分歧:上游 exporterhelper README 写 5000(旧值),
官网 resiliency 页写"often 1000";新增的 sizer 字段还改变计量单位。见 README。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

# 官方 queue_size 建议公式里用的换算口径:queue_size = 秒数 × 每秒请求数 / 每批请求数


@dataclass
class RetryPolicy:
    enabled: bool = True
    initial_interval: float = 5.0
    max_interval: float = 30.0
    max_elapsed_time: float = 300.0
    multiplier: float = 1.5

    def backoff(self, attempt):
        """第 attempt 次重试前的等待秒数(attempt 从 1 起)。"""
        if not self.enabled:
            return float("inf")
        raw = self.initial_interval * (self.multiplier ** (attempt - 1))
        return min(raw, self.max_interval)

    def schedule(self, n):
        return [self.backoff(k) for k in range(1, n + 1)]

    def total_wait(self, n):
        return sum(self.schedule(n))

    def attempts_within_budget(self):
        """在 max_elapsed_time 内最多能重试几次(累计等待不超预算)。

        max_elapsed_time = 0 时官方语义为"永不停止",这里返回 -1 表示无上限。
        """
        if not self.enabled:
            return 0
        if self.max_elapsed_time <= 0:
            return -1
        total, k = 0.0, 0
        while True:
            nxt = total + self.backoff(k + 1)
            if nxt > self.max_elapsed_time:
                return k
            total, k = nxt, k + 1

    def window_covers(self, outage_s):
        """给定后端故障时长,重试窗口能否覆盖到它恢复(-1 预算表示总能覆盖)。"""
        budget = self.attempts_within_budget()
        if budget < 0:
            return True
        return self.total_wait(budget) >= outage_s


class SendingQueue:
    """内存发送队列。队列满且未开 block_on_overflow 时新数据被直接丢弃。

    丢弃发生在进入重试逻辑之前,因此不计入重试,由
    `otelcol_exporter_enqueue_failed_*` 指标暴露。
    """

    def __init__(self, queue_size=5000, num_consumers=10, block_on_overflow=False):
        self.queue_size = queue_size
        self.num_consumers = num_consumers
        self.block_on_overflow = block_on_overflow
        self._q = deque()
        self.enqueued = 0
        self.enqueue_failed = 0

    def __len__(self):
        return len(self._q)

    @property
    def capacity_left(self):
        return self.queue_size - len(self._q)

    def enqueue(self, batch):
        """返回 True=入队成功;False=被丢弃;'blocked'=调用方阻塞等待。"""
        if len(self._q) >= self.queue_size:
            if self.block_on_overflow:
                return "blocked"
            self.enqueue_failed += 1
            return False
        self._q.append(batch)
        self.enqueued += 1
        return True

    def enqueue_many(self, batches):
        out = []
        for b in batches:
            out.append(self.enqueue(b))
        return out

    def drain(self, per_batch_s=1.0):
        """按 num_consumers 并发消费,返回清空队列所需秒数。"""
        if not self._q:
            return 0.0
        rounds = (len(self._q) + self.num_consumers - 1) // self.num_consumers
        self._q.clear()
        return rounds * per_batch_s


def suggested_queue_size(buffer_seconds, requests_per_second, per_batch):
    """官方给出的 queue_size 估算公式:秒数 × RPS ÷ 每批请求数。"""
    if per_batch <= 0:
        raise ValueError("per_batch 必须为正")
    return int(buffer_seconds * requests_per_second / per_batch)


class PersistentQueue(SendingQueue):
    """file_storage 支撑的持久化队列:进程崩溃后重启继续投递。

    与内存队列的差别只在"崩溃时已入队的批次是否还在":内存队列全丢,
    持久化队列保留(磁盘故障 / 空间耗尽仍会丢)。
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.on_disk = deque()
        self.loaded_batches = 0

    def reload_after_crash(self):
        """重启:把盘上批次读回内存队列(顺序保持)。"""
        self.loaded_batches = len(self.on_disk)
        for b in self.on_disk:
            self._q.append(b)
        self.on_disk.clear()
        return self.loaded_batches

    def crash_and_restart(self):
        """进程崩溃后重启:内存队列全丢,再从盘上恢复。"""
        self._q.clear()
        return self.reload_after_crash()

    def enqueue(self, batch):
        ok = super().enqueue(batch)
        if ok is True:
            self.on_disk.append(batch)
        return ok


def memory_queue_crash_loss(queued_batches):
    """纯内存队列崩溃时已入队批次的损失量(全丢),用作持久化队列的对照。"""
    return len(queued_batches)
