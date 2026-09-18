"""Prometheus TSDB 的存储模型：ULID 块命名、块布局、压缩计划、WAL、倒排索引。

事实依据（Prometheus 官方 `docs/storage.md`，本机实读）：

* 摄入样本按 **2 小时** 分组为块；块目录含 `chunks/`（该时间窗全部序列的样本）、
  元数据文件与 `index`（把指标名/标签映射到 chunks 里的序列）。`chunks/` 内按
  **每段最大 512 MB** 切分为若干段文件。
* 删除记录写进独立的 `tombstones` 文件，而不是立即从 chunk 段里抹掉。
* 当前块在内存中，靠 **WAL** 保证崩溃可恢复；WAL 文件位于 `wal/`，**每段 128 MB**，
  且至少保留 **3** 个 WAL 段；高流量实例会保留更多以保证至少 **2 小时**的原始数据。
* 压缩：初始 2h 块后台合并成更长的块，跨度上限为
  **min(保留期的 10%, 31 天)**；压缩期间源块与新块必须共存，故磁盘占用会短暂超过
  `retention.size`。官方建议 `retention.size` 设为分配磁盘的 **80~85%**。
* 容量公式：`needed_disk_space = retention_time_seconds * ingested_samples_per_second
  * bytes_per_sample`，每样本平均只占 **1~2 字节**；默认保留期 **15d**。
* 时间保留与容量保留同时配置时，**先触发者生效**；过期块清理是后台行为，最多可能
  耗时 **2 小时**，且块必须**整块过期**才会被删除。
* WAL 记录类型（`tsdb/record` 包）：Series / Samples / Histograms / FloatHistograms /
  Exemplars / Tombstones / Metadata / MmapMarkers；另有 `wbl/` 存放乱序样本。
* 重放从最新 `checkpoint.NNNNNN`（NNNNNN 为该 checkpoint 覆盖到的段号）+ 之后的
  段文件开始；重放按 series ref 分片并行（worker 数默认 GOMAXPROCS）。
* 倒排索引：`label_name → label_value → 升序 series ref 列表`，多 matcher 取交集，
  有序表可用 merge-join 做到 O(n+m)。
* 备份：推荐用快照（snapshot）。不做快照而直接拷目录时，若排除 `wal/`、
  `chunks_head/`、`wbl/`，则丢失「自上一个块创建以来」的样本（块通常每 2 小时创建，
  官方表述为覆盖最近 **3 小时**样本）。
* 不支持的 FS：非 POSIX 文件系统（官方点名 NFS，含 AWS EFS）。
"""

import math

#: 块时间窗（官方的「2 小时块」）
BLOCK_DURATION = 2 * 3600.0
#: chunk 段文件上限（字节）
CHUNK_SEGMENT_SIZE = 512 * 1024 * 1024
#: WAL 段文件上限（字节）
WAL_SEGMENT_SIZE = 128 * 1024 * 1024
#: 至少保留的 WAL 段数
MIN_WAL_SEGMENTS = 3
#: 默认保留期
DEFAULT_RETENTION = 15 * 86400.0
#: 压缩后块跨度的绝对上限
MAX_COMPACTED_SPAN = 31 * 86400.0

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


# ------------------------------------------------------------------ ULID
def ulid_encode(ts_ms, rand80):
    """把 48 位毫秒时间戳与 80 位随机数编码成 26 字符 ULID（Crockford Base32）。"""
    if not 0 <= ts_ms < (1 << 48):
        raise ValueError("时间戳超出 48 位范围")
    if not 0 <= rand80 < (1 << 80):
        raise ValueError("随机数超出 80 位范围")
    v = (ts_ms << 80) | rand80
    return "".join(_CROCKFORD[(v >> (5 * (25 - i))) & 0x1F] for i in range(26))


def ulid_decode(text):
    """返回 (时间戳毫秒, 80 位随机数)。"""
    if len(text) != 26:
        raise ValueError("ULID 长度必须为 26")
    v = 0
    for ch in text:
        v = (v << 5) | _CROCKFORD.index(ch)
    return v >> 80, v & ((1 << 80) - 1)


# ------------------------------------------------------------ 块与压缩计划
def block_bounds(start_ts):
    """块的半开时间区间 [start, start + 2h)。"""
    return start_ts, start_ts + BLOCK_DURATION


def max_compacted_span(retention_s):
    """压缩后单块跨度上限 = min(保留期 10%, 31 天)。"""
    return min(0.10 * retention_s, MAX_COMPACTED_SPAN)


def compaction_ladder(retention_s):
    """从 2h 起按 2 倍递增，列出不超过上限的各层块时长（秒）。"""
    cap = max_compacted_span(retention_s)
    out, d = [], BLOCK_DURATION
    while d <= cap + 1e-9:
        out.append(d)
        d *= 2
    return out


def peak_bytes_during_compaction(source_sizes):
    """压缩期间源块与新块共存 —— 新块体积不超过全部源块体积之和。"""
    return sum(source_sizes) + sum(source_sizes)


# ------------------------------------------------------------------ 容量
def needed_disk_space(retention_s, samples_per_s, bytes_per_sample):
    """官方容量公式（含每样本 1~2 字节的经验值口径）。"""
    return retention_s * samples_per_s * bytes_per_sample


def series_reduction_vs_interval(n_series, per_series_overhead, samples_per_series,
                                 bytes_per_sample, bytes_per_sample_at_half=None):
    """比较「序列数减半」与「抓取间隔翻倍」两种降采样手段的成本。

    官方只说「减少序列数**很可能**更有效，因为同一序列内样本会被压缩」。本函数给出
    可量化的原因：① 每条序列有**固定开销**（索引项 / chunk 头），序列数减半直接省掉
    一半；② 抓取间隔翻倍虽把样本数减半，但若压缩率随序列内样本数下降（`bytes_per_sample_at_half`
    更大），省下的比例就**到不了一半**。参数 `bytes_per_sample_at_half=None` 表示压缩率
    与序列长度无关的对照口径。
    """
    if bytes_per_sample_at_half is None:
        bytes_per_sample_at_half = bytes_per_sample
    by_halving = (n_series / 2.0) * (per_series_overhead
                                     + samples_per_series * bytes_per_sample)
    by_interval = n_series * (per_series_overhead
                              + samples_per_series / 2.0 * bytes_per_sample_at_half)
    return by_halving, by_interval


def wal_segments_for(seconds, samples_per_s, wal_bytes_per_sample):
    """覆盖 ``seconds`` 秒所需 WAL 段数；官方规定至少保留 3 段。"""
    raw = seconds * samples_per_s * wal_bytes_per_sample
    return max(MIN_WAL_SEGMENTS, int(math.ceil(raw / WAL_SEGMENT_SIZE)))


def which_retention_fires(retention_time_s, bytes_accumulated, bytes_per_second,
                          retention_size_bytes):
    """时间与容量两条保留策略同时配置时，先触发者生效。

    返回 ``"time"`` / ``"size"`` / ``None``。
    """
    if retention_time_s is not None and retention_time_s <= 0:
        return "time"
    if retention_size_bytes and bytes_accumulated >= retention_size_bytes:
        return "size"
    if not retention_size_bytes:
        return "time" if retention_time_s is not None else None
    if bytes_per_second <= 0:
        return None
    t_hit_size = (retention_size_bytes - bytes_accumulated) / bytes_per_second
    if retention_time_s is not None and retention_time_s <= t_hit_size:
        return "time"
    return "size"


class Block:
    """一个不可变磁盘块。"""

    __slots__ = ("ulid", "start", "end", "num_samples", "num_series", "level", "sources")

    def __init__(self, ulid, start, num_samples, num_series, level=1, sources=()):
        self.ulid = ulid
        self.start = start
        self.end = start + BLOCK_DURATION
        self.num_samples = num_samples
        self.num_series = num_series
        self.level = level
        self.sources = list(sources)

    @property
    def size_bytes(self):
        return self.num_samples * 1.5  # 官方口径：每样本平均 1~2 字节

    def is_expired(self, now, retention_s):
        return self.end < now - retention_s

    def chunk_segments(self):
        return max(1, int(math.ceil(self.size_bytes / CHUNK_SEGMENT_SIZE)))


def pick_compaction(blocks, retention_s):
    """挑出可压缩的一组块：同一层、数量 >= 2、总跨度不超过上限。"""
    cap = max_compacted_span(retention_s)
    groups = {}
    for b in blocks:
        groups.setdefault(b.level, []).append(b)
    for level in sorted(groups):
        cand = sorted(groups[level], key=lambda b: b.ulid)
        for i in range(len(cand)):
            span, sel = 0.0, []
            for b in cand[i:]:
                if span + BLOCK_DURATION > cap:
                    break
                span += BLOCK_DURATION
                sel.append(b)
            if len(sel) >= 2:
                return level, sel
    return None, []


# ------------------------------------------------------------ 倒排索引
class InvertedIndex:
    """label_name=label_value → 升序 series ref 列表。"""

    def __init__(self):
        self.postings = {}
        self.labels_of = {}

    def add(self, ref, labels):
        self.labels_of[ref] = dict(labels)
        for k, v in labels.items():
            self.postings.setdefault((k, v), []).append(ref)
        for lst in self.postings.values():
            lst.sort()

    def intersect(self, matchers):
        """matchers: [(label, value), ...]；返回 (命中 ref 升序列表, 比较次数)。"""
        lists = [self.postings.get((k, v), []) for k, v in matchers]
        if not lists:
            return sorted(self.labels_of), 0
        lists.sort(key=len)
        cur, comparisons = list(lists[0]), 0
        for other in lists[1:]:
            merged, i, j = [], 0, 0
            while i < len(cur) and j < len(other):
                comparisons += 1
                if cur[i] == other[j]:
                    merged.append(cur[i])
                    i += 1
                    j += 1
                elif cur[i] < other[j]:
                    i += 1
                else:
                    j += 1
            cur = merged
            if not cur:
                break
        return cur, comparisons


# --------------------------------------------------------------- WAL
def wal_shards(refs, workers):
    """重放按 series ref 分片；每个 ref 恰好归属一个 worker。"""
    shards = [[] for _ in range(workers)]
    for r in refs:
        shards[r % workers].append(r)
    return shards


def replay_range(checkpoints, segments):
    """重放起点：最新 checkpoint 覆盖到的段号之后的所有段。"""
    if not checkpoints:
        return list(segments)
    last = max(checkpoints)
    return [s for s in segments if s > last]


def backup_loss_window(blocks, head_seconds=2 * 3600.0):
    """不做快照而排除 wal/ 时，丢失的时间范围（官方表述：最近 3 小时）。"""
    last_block_end = max(b.end for b in blocks) if blocks else 0.0
    return last_block_end, last_block_end + head_seconds


SUPPORTED_POSIX_FS = {"ext4", "xfs", "btrfs", "zfs", "f2fs", "tmpfs", "apfs"}
UNSUPPORTED_FS = {"nfs", "efs", "smb", "cifs"}
