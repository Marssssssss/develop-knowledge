"""Ingester 写入路径：时序校验、去重、chunk 滚动、WAL、崩溃恢复。

写入路径的三条硬规则（来自官方 architecture overview）：

1. **同一 stream 的日志时间戳必须严格递增**，否则该行被拒并回错给客户端。
2. **完全重复的行（时间戳与内容都相同）被静默忽略**，不算错误。
3. **时间戳相同但内容不同的行会被接受** —— 所以同一条流里可以存在两条
   时间戳相同的日志。很多人以为「同 ts 一律拒绝」，这是错的。

chunk 的编码、体积与滚动条件在 loki_chunk 里，此处 re-export。
"""

from __future__ import annotations

from dataclasses import dataclass

from loki_chunk import (  # noqa: F401  —— 统一从本模块 re-export
    CHUNK_DEFAULTS,
    DEFAULT_ENCODING,
    ENCODINGS,
    ILLUSTRATIVE_RATIO,
    Chunk,
    Stream,
    chunk_object_key,
    fingerprint,
    quorum,
)

__all__ = [
    "CHUNK_DEFAULTS",
    "DEFAULT_ENCODING",
    "ENCODINGS",
    "ILLUSTRATIVE_RATIO",
    "Chunk",
    "Stream",
    "chunk_object_key",
    "fingerprint",
    "quorum",
    "WALRecord",
    "WAL",
    "Ingester",
]


@dataclass
class WALRecord:
    tenant: str
    labels: tuple[tuple[str, str], ...]
    ts_ns: int
    line: str


class WAL:
    """写入前日志。崩溃后重放未刷写记录；优雅关闭则先刷写、WAL 变空。

    `checkpoint_duration` 默认 5m：定期把当前段折叠进 checkpoint，避免重放
    时间随运行时长线性增长。
    """

    def __init__(self, checkpoint_duration_s: float = 300.0) -> None:
        self.checkpoint_duration_s = checkpoint_duration_s
        self.records: list[WALRecord] = []
        self.replayed_from: int = 0
        self.last_checkpoint_ns: int | None = None
        self.checkpoints: int = 0

    def append(self, tenant: str, labels: tuple, ts_ns: int, line: str) -> None:
        self.records.append(WALRecord(tenant, labels, ts_ns, line))
        if self.last_checkpoint_ns is None:
            self.last_checkpoint_ns = ts_ns

    def checkpoint(self, now_ns: int) -> bool:
        """到周期则折叠一次，返回是否真的折叠了。"""
        if self.last_checkpoint_ns is None:
            return False
        if now_ns - self.last_checkpoint_ns < int(self.checkpoint_duration_s * 1e9):
            return False
        self.replayed_from = len(self.records)
        self.last_checkpoint_ns = now_ns
        self.checkpoints += 1
        return True

    def pending(self) -> list[WALRecord]:
        return self.records[self.replayed_from :]

    def mark_flushed(self) -> None:
        """数据已确认落到对象存储（如优雅关闭时全量刷写），WAL 可整体截断。"""
        self.replayed_from = len(self.records)


class Ingester:
    """单实例 ingester。内存里按 (tenant, labels) 维护 Stream。"""

    def __init__(
        self,
        *,
        encoding: str = DEFAULT_ENCODING,
        target_size: int = 1572864,
        idle_period_s: float = 1800.0,
        max_age_s: float = 7200.0,
        wal_enabled: bool = True,
    ) -> None:
        if encoding not in ENCODINGS:
            raise ValueError(f"未知的 chunk_encoding: {encoding!r}")
        self.encoding = encoding
        self.target_size = target_size
        self.idle_period_s = idle_period_s
        self.max_age_s = max_age_s
        self.wal = WAL() if wal_enabled else None
        self.streams: dict[tuple[str, tuple], Stream] = {}

    def stream(self, tenant: str, labels: tuple[tuple[str, str], ...]) -> Stream:
        key = (tenant, labels)
        if key not in self.streams:
            self.streams[key] = Stream(
                tenant=tenant,
                labels=labels,
                encoding=self.encoding,
                target_size=self.target_size,
                idle_period_s=self.idle_period_s,
                max_age_s=self.max_age_s,
            )
        return self.streams[key]

    def push(self, tenant: str, labels: tuple, ts_ns: int, line: str) -> str:
        outcome = self.stream(tenant, labels).push(ts_ns, line)
        if outcome == "appended" and self.wal is not None:
            self.wal.append(tenant, labels, ts_ns, line)
        return outcome

    def tick(self, now_ns: int) -> list[Chunk]:
        """周期性检查（对应 flush_check_period）：按滚动条件刷写。"""
        flushed: list[Chunk] = []
        for stream in self.streams.values():
            reasons = stream.flush_reasons(now_ns)
            if reasons:
                chunk = stream.flush(now_ns, reasons[0])
                if chunk is not None:
                    flushed.append(chunk)
        return flushed

    def shutdown(self, now_ns: int) -> list[Chunk]:
        """优雅关闭：flush_on_shutdown=true 时把内存里的流全部刷写，并截断 WAL。"""
        flushed: list[Chunk] = []
        for stream in self.streams.values():
            if stream.entries:
                chunk = stream.flush(now_ns, "shutdown")
                if chunk is not None:
                    flushed.append(chunk)
        if self.wal is not None:
            self.wal.mark_flushed()
        return flushed

    def crash(self) -> None:
        """进程异常退出：**内存里的流全部丢失**，只有已落盘的 WAL 还在。

        chunk_retain_period 默认 0s，刷写后不留内存副本；因此「未刷写的 chunk」
        在崩溃时就是真丢 —— 这就是默认 replication_factor=3 存在的理由。
        """
        self.streams.clear()

    def recover(self) -> dict[tuple[str, tuple], int]:
        """崩溃恢复：重放 WAL 里尚未被 flush 的记录，返回每条流恢复的条数。"""
        restored: dict[tuple[str, tuple], int] = {}
        if self.wal is None:
            return restored
        for rec in self.wal.pending():
            self.stream(rec.tenant, rec.labels).push(rec.ts_ns, rec.line)
            restored[(rec.tenant, rec.labels)] = restored.get((rec.tenant, rec.labels), 0) + 1
        return restored
