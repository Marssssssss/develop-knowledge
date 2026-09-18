"""processor 与 pipeline 运行语义:memory_limiter / batch / attributes / filter。

默认值取自官方与厂商文档(见 ../README.md「参考资料」):
  batch      timeout=200ms  send_batch_size=8192  send_batch_max_size=0(不限)
  limiter    check_interval=0s  limit_mib=0  spike_limit_mib=limit_mib 的 20%
凡文档未给出默认值的,一律在本文件注释里标注为「建模假设」。
"""

from __future__ import annotations

from dataclasses import dataclass

from otel_config import ConfigError
from otel_transform import AttributesProcessor, FilterProcessor, eval_condition  # noqa: F401

# ---------------------------------------------------------------- 数据模型


@dataclass
class Record:
    """一条 telemetry 记录(trace span / metric point / log record 的统一抽象)。"""

    attrs: dict

    def copy(self):
        return Record(dict(self.attrs))


# ---------------------------------------------------------------- memory_limiter


class MemoryLimiter:
    """周期检查进程内存:超软限拒绝入站数据并回压,超硬限强制 GC。

    软限 = limit_mib - spike_limit_mib。真实实现里这一比较是 `>`(严格大于),
    等号边界属建模选择;check_interval 为 0s 时官方建议显式设为 1s。
    """

    def __init__(self, limit_mib=0, spike_limit_mib=None, check_interval_s=0.0):
        self.limit_mib = limit_mib
        self.spike_limit_mib = (
            round(limit_mib * 0.2) if spike_limit_mib is None else spike_limit_mib
        )
        self.check_interval_s = check_interval_s

    @property
    def soft_limit_mib(self):
        return self.limit_mib - self.spike_limit_mib

    def check(self, rss_mib):
        """返回 `(verdict, headroom_mib)`;verdict ∈ ok / refused / gc。"""
        if self.limit_mib <= 0:
            return "ok", float("inf")
        headroom = self.soft_limit_mib - rss_mib
        if rss_mib > self.limit_mib:
            return "gc", headroom
        if rss_mib > self.soft_limit_mib:
            return "refused", headroom
        return "ok", headroom


# ---------------------------------------------------------------- batch


class BatchProcessor:
    """把记录聚成批以减少出站调用;`send_batch_size` 触发或 `timeout` 兜底。

    `send_batch_max_size` 非 0 时对每批做切分,尾批可以小于 send_batch_size
    (真实实现允许尾批不足)。max_size 必须 >= send_batch_size,否则配置非法。
    """

    def __init__(self, send_batch_size=8192, timeout_ms=200, send_batch_max_size=0):
        if send_batch_max_size and send_batch_max_size < send_batch_size:
            raise ConfigError(
                "send_batch_max_size(%d) 必须 >= send_batch_size(%d)"
                % (send_batch_max_size, send_batch_size)
            )
        self.send_batch_size = send_batch_size
        self.timeout_ms = timeout_ms
        self.send_batch_max_size = send_batch_max_size
        self.buf = []
        self.first_add_ms = None
        self.emitted = []  # [(reason, [batch_size, ...])]

    def add(self, rec, now_ms):
        self.buf.append(rec)
        if self.first_add_ms is None:
            self.first_add_ms = now_ms
        if len(self.buf) >= self.send_batch_size:
            return self.flush(now_ms, "size")
        return []

    def extend(self, records, now_ms):
        """逐条到达(每条都检查阈值),模拟一条一条被 receiver 收上来。"""
        out = []
        for r in records:
            out.extend(self.add(r, now_ms))
        return out

    def push(self, records, now_ms):
        """一次投递 N 条后检查阈值。

        真实 Collector 的输入单元是一个 request(可能含成千上万 spans/metrics/
        log records),所以 send_batch_max_size 的切分只在 push 语义下才发生:
        逐条 add 时缓冲永远刚好到 send_batch_size 就发了。
        """
        self.buf.extend(records)
        if self.first_add_ms is None and records:
            self.first_add_ms = now_ms
        if len(self.buf) >= self.send_batch_size:
            return self.flush(now_ms, "size")
        return []

    def tick(self, now_ms):
        if self.buf and now_ms - self.first_add_ms >= self.timeout_ms:
            return self.flush(now_ms, "timeout")
        return []

    def flush(self, now_ms, reason):
        out = []
        while self.buf:
            n = len(self.buf)
            if self.send_batch_max_size:
                n = min(n, self.send_batch_max_size)
            out.append(self.buf[:n])
            del self.buf[:n]
        self.first_add_ms = None
        if out:
            self.emitted.append((reason, [len(b) for b in out]))
        return out


# ---------------------------------------------------------------- pipeline


class PipelineRunner:
    """一条 pipeline 的 processor 链执行器。

    设计取舍:batch 不参与逐记录变换,单独作为出站缓冲器建模(真实实现里它也
    是"把记录攒起来再交给下游",不在记录级改写)。
    """

    def __init__(self, key, processors):
        self.key = key
        self.processors = list(processors)
        self.seen = 0
        self.refused = 0
        self.filtered = 0
        self.gc_forced = 0

    def limiter(self):
        return next((p for p in self.processors if isinstance(p, MemoryLimiter)), None)

    @property
    def limiter_is_first(self):
        return bool(self.processors) and isinstance(self.processors[0], MemoryLimiter)

    def run(self, records, rss_mib=None):
        """返回放行记录;被 memory_limiter / filter 拦掉的不在结果里。"""
        self.seen += len(records)
        lm = self.limiter()
        if lm is not None and rss_mib is not None:
            verdict, _ = lm.check(rss_mib)
            if verdict == "gc":
                self.gc_forced += 1
                self.refused += len(records)
                return []
            if verdict == "refused":
                self.refused += len(records)
                return []
        out = []
        for rec in records:
            r = rec.copy()
            drop = False
            for p in self.processors:
                if isinstance(p, FilterProcessor) and p.matches(r):
                    drop = True
                    break
                if isinstance(p, AttributesProcessor):
                    p.apply(r)
            if drop:
                self.filtered += 1
            else:
                out.append(r)
        return out


def fanout_export(batches, exporters):
    """pipeline 内多 exporter:同一份数据广播给每个分支(同步调用)。

    exporters 为 `{name: callable(batch)->bool}` 时返回每个分支的逐批结果。
    """
    return {name: [send(b) for b in batches] for name, send in exporters.items()}


def fanout_latency_ms(branch_ms, parallel):
    """同一次扇出的耗时:串行为求和,并行为最慢分支(尾部延迟)。"""
    if not branch_ms:
        return 0
    return max(branch_ms) if parallel else sum(branch_ms)


def wasted_work(processors_after_limiter, n_records):
    """limiter 拒绝整批时,它前面那些组件已经白做的工作量(以"次操作"计)。

    memory_limiter 放首位 -> 0;放在第 k 位 -> 前 k-1 个组件已经为这 n 条记录
    跑过一遍。权重是建模假设(条件求值按 2 次操作、attributes 按动作数计)。
    """
    cost = 0
    for p in processors_after_limiter:
        if isinstance(p, FilterProcessor):
            cost += n_records * 2 * len(p.conditions)
        elif isinstance(p, AttributesProcessor):
            cost += n_records * len(p.actions)
    return cost
