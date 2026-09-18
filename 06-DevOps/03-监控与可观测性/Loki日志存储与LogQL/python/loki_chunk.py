"""Chunk 模型：编码、压缩、滚动条件、对象键与复制仲裁。

chunk 滚动的三个触发条件相互独立，任一满足即刷写：空闲超时
（`chunk_idle_period` 30m）、压缩后体积达标（`chunk_target_size` 1.5 MiB）、
存活超时（`max_chunk_age` 2h）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

__all__ = [
    "CHUNK_DEFAULTS",
    "ENCODINGS",
    "ILLUSTRATIVE_RATIO",
    "DEFAULT_ENCODING",
    "Chunk",
    "Stream",
    "quorum",
    "chunk_object_key",
    "fingerprint",
]

# ingester 块默认值。来源：官方配置参考手册（逐项标注 default =）与官方
# 「Configuration best practices」推荐清单。
CHUNK_DEFAULTS: dict[str, object] = {
    "chunk_idle_period": 1800.0,  # 30m
    "chunk_retain_period": 0.0,
    "chunk_block_size": 262144,  # 256 KiB —— 未压缩上限
    "chunk_target_size": 1572864,  # 1.5 MiB —— 压缩后目标
    "max_chunk_age": 7200.0,  # 2h
    "chunk_encoding": "gzip",
    "concurrent_flushes": 32,
    "flush_op_timeout": 600.0,  # 10m
    "sync_period": 3600.0,  # 1h
    "sync_min_utilization": 0.1,
}

ENCODINGS = (
    "none",
    "gzip",
    "lz4-64k",
    "snappy",
    "lz4-256k",
    "lz4-1M",
    "lz4",
    "flate",
    "zstd",
)
# 默认是 gzip；官方最佳实践**推荐** snappy（更快、略大）。推荐值 ≠ 默认值 ——
# 配置文件里不写就是 gzip。
DEFAULT_ENCODING = "gzip"

# 说明性压缩比，**不是实测值**，只用来演示「压缩后体积达标就滚动」这条机制。
# 真实比值强烈依赖日志内容：官方没给数字，业界公开经验是高度重复的结构化
# JSON 大致 10~15 倍、自由文本大致 3~6 倍。断言只依赖比值参数本身，
# 不把任何比值当成事实。
ILLUSTRATIVE_RATIO = {
    "none": 1.0,
    "snappy": 2.0,
    "lz4": 2.2,
    "lz4-64k": 2.0,
    "lz4-256k": 2.4,
    "lz4-1M": 2.6,
    "flate": 5.5,
    "gzip": 6.0,
    "zstd": 6.5,
}


def fingerprint(labels: tuple[tuple[str, str], ...]) -> str:
    """流的指纹。

    Loki 用 xxhash64 并以十进制字符串呈现。这里用 SHA-256 前 8 字节代替，
    只为在无第三方依赖下取得稳定短标识；**不声称与 Loki 同值**。
    """
    payload = "\x00".join(f"{k}\x01{v}" for k, v in labels)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def chunk_object_key(
    tenant: str, labels: tuple[tuple[str, str], ...], from_ns: int, to_ns: int, checksum: str
) -> str:
    """对象存储里的 chunk 键。租户前缀在最外层——这是存储层租户隔离的物理依据。"""
    return f"{tenant}/{fingerprint(labels)}/{from_ns}:{to_ns}:{checksum}"


def quorum(replication_factor: int) -> int:
    """Dynamo 风格仲裁数：floor(rf/2) + 1。rf=3 → 需要 2 个成功。"""
    if replication_factor < 1:
        raise ValueError("replication_factor 必须 >= 1")
    return replication_factor // 2 + 1


@dataclass
class Chunk:
    """一个已刷写的 chunk 记录（元数据，不含真实压缩字节）。"""

    tenant: str
    labels: tuple[tuple[str, str], ...]
    from_ns: int
    to_ns: int
    entries: int
    uncompressed_bytes: int
    encoding: str
    reason: str

    @property
    def compressed_bytes(self) -> int:
        return int(self.uncompressed_bytes / ILLUSTRATIVE_RATIO[self.encoding])

    @property
    def object_key(self) -> str:
        return chunk_object_key(
            self.tenant, self.labels, self.from_ns, self.to_ns, f"{self.entries:x}"
        )


@dataclass
class Stream:
    """一条流（唯一标签组合）在 ingester 内存中的状态。"""

    tenant: str
    labels: tuple[tuple[str, str], ...]
    encoding: str = DEFAULT_ENCODING
    target_size: int = 1572864
    idle_period_s: float = 1800.0
    max_age_s: float = 7200.0
    entries: list[tuple[int, str]] = field(default_factory=list)
    chunk_start_ns: int | None = None
    last_append_ns: int | None = None
    flushed: list[Chunk] = field(default_factory=list)
    rejected_out_of_order: int = 0
    ignored_duplicates: int = 0

    def push(self, ts_ns: int, line: str) -> str:
        """返回 appended | duplicate | out_of_order。

        去重只认「紧邻的上一行」——与官方描述一致（比较对象是 previous line），
        不是对整个 chunk 做全局去重。
        """
        if self.entries:
            last_ts, last_line = self.entries[-1]
            if ts_ns < last_ts:
                self.rejected_out_of_order += 1
                return "out_of_order"
            if ts_ns == last_ts:
                if line == last_line:
                    self.ignored_duplicates += 1
                    return "duplicate"
                # 同 ts 不同内容：合法，继续走追加
        else:
            self.chunk_start_ns = ts_ns
        self.entries.append((ts_ns, line))
        self.last_append_ns = ts_ns
        return "appended"

    @property
    def uncompressed_bytes(self) -> int:
        # 每条 +16 字节近似 per-entry 头部开销，只为让体积随行数单调增长。
        return sum(len(line.encode("utf-8")) + 16 for _ts, line in self.entries)

    @property
    def compressed_bytes(self) -> int:
        return int(self.uncompressed_bytes / ILLUSTRATIVE_RATIO[self.encoding])

    def flush_reasons(self, now_ns: int) -> list[str]:
        if not self.entries:
            return []
        reasons: list[str] = []
        if self.last_append_ns is not None:
            if now_ns - self.last_append_ns >= int(self.idle_period_s * 1e9):
                reasons.append("idle")
        if self.compressed_bytes >= self.target_size:
            reasons.append("size")
        if self.chunk_start_ns is not None:
            if now_ns - self.chunk_start_ns >= int(self.max_age_s * 1e9):
                reasons.append("age")
        return reasons

    def flush(self, _now_ns: int, reason: str) -> Chunk | None:
        if not self.entries:
            return None
        chunk = Chunk(
            tenant=self.tenant,
            labels=self.labels,
            from_ns=self.entries[0][0],
            to_ns=self.entries[-1][0],
            entries=len(self.entries),
            uncompressed_bytes=self.uncompressed_bytes,
            encoding=self.encoding,
            reason=reason,
        )
        self.flushed.append(chunk)
        # 刷写后新建空 chunk 继续接收（chunk_retain_period 默认 0s，
        # 即刷完不再驻留内存）
        self.entries = []
        self.chunk_start_ns = None
        self.last_append_ns = None
        return chunk
