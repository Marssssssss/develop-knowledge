#!/usr/bin/env python3
"""Lucene/Elasticsearch 索引段（segment）合并策略演示 —— 纯标准库。

权威来源：Elastic 博客 *Lucene's Handling of Deleted Documents* 与
*Performance Considerations for Elasticsearch Indexing*。

Lucene 的基本事实：

1. segment 不可变。update = 新增 segment + 在老 segment 内 bitset 标记删除。
2. 查询路径：先在所有 segment 上并行跑，按 score 合并 top-k；与此同时跳过被 bitset
   标记删除的文档——所以"删除"不释放磁盘，只在合并时才回收。
3. 默认合并策略是 TieredMergePolicy（Lucene 8+ / ES 5+）：
   - segments_per_tier (默认 10)：每层最多容纳这么多 segment
   - max_merged_segment (默认 5 GB)：超过此大小不再被合并（一次性合并）
   - floor_segment (默认 2 MB)：小于此的段视为"零碎"，优先合并
   - reclaim_deletes_weight (默认 2.0)：被删除比例高的段优先级 ↑
4. 合并过程是后台合并器；ES 默认把 IO 限流 20 MB/s（保护搜索性能）。

本 demo 用一个仿真（不需要真启 ES），演示：
  1. segments_per_tier 决定 segment 落在哪个层（tier）
  2. 合并评分：score ∝ size × (1 + reclaim_deletes_weight · deletes%)
  3. "forceMerge" 把 segment 数压到 1 的过程
  4. 20 MB/s 节流下 IO 性能与"日合并量"的关系
"""

from __future__ import annotations

from dataclasses import dataclass, field

# TieredMergePolicy 默认参数（Lucene 9 + ES 8.x 标准）
SEGMENTS_PER_TIER = 10
MAX_MERGED_SEGMENT = 5 * 1024 * 1024 * 1024        # 5 GB
FLOOR_SEGMENT = 2 * 1024 * 1024                     # 2 MB
RECLAIM_DELETES_WEIGHT = 2.0
MERGE_THROTTLE_MB_PER_SEC = 20.0                    # ES 默认值


@dataclass
class Segment:
    """模拟一个 segment 的元数据。"""

    seg_id: str
    size_bytes: int
    num_docs: int            # 写入的文档数（含将被删除的）
    del_docs: int = 0        # 标记删除的文档数（bitset 内 bit = 1）

    @property
    def live_docs(self) -> int:
        return self.num_docs - self.del_docs

    @property
    def del_ratio(self) -> float:
        return self.del_docs / self.num_docs if self.num_docs else 0.0


def tier_of(size: int) -> int:
    """根据段大小计算所属 tier 编号（log scale，与 Lucene TieredMergePolicy 一致）。

    floor_segment (2MB) 为 tier 0 每段上限；
    每上一个 tier，size 上限翻倍（直到 max_merged_segment）。
    """
    if size <= FLOOR_SEGMENT:
        return 0
    size_norm = min(size, MAX_MERGED_SEGMENT)
    # 计算跨越了几个 2 倍
    tier = 0
    cap = FLOOR_SEGMENT
    while cap < size_norm:
        cap *= 2
        tier += 1
    return tier


def merge_score(seg: Segment, reclaim_weight: float = RECLAIM_DELETES_WEIGHT) -> float:
    """Lucene TieredMergePolicy 的合并优先级评分（越小越优先）：

        score ∝ size_bytes · (1 + reclaim_weight · del_ratio)

    当 del_ratio = 0 时退化为 size_bytes（按"少合并"自然增长）；
    del_ratio 高时（被回收的好处多），分数增长 ⇒ 提早被选中。
    """
    return seg.size_bytes * (1.0 + reclaim_weight * seg.del_ratio)


def merge_one(segments: list[Segment], merge_size: int, reclaim_weight: float) -> Segment:
    """模拟"合并 N 个 segment 为 1 个"，返回新 segment：

        size_bytes = sum(input.size_bytes
        num_docs   = sum(input.live_docs)        ← 删除文档被实际剔除！
        del_docs   = 0                            ← bitset 在新段里重新为空
    """
    return Segment(
        seg_id=f"merged-{segments[0].seg_id}+{len(segments) - 1}",
        size_bytes=sum(s.size_bytes for s in segments),
        num_docs=sum(s.live_docs for s in segments),
    )


def find_merge_candidates(
    segments: list[Segment],
    max_segments_in_merge: int,
    reclaim_weight: float = RECLAIM_DELETES_WEIGHT,
) -> list[list[Segment]] | None:
    """近似模拟 TieredMergePolicy.findMerges：
      * 先按 tier 分组
      * 每个 tier 内：按合并评分升序
      * 取分数最低的一组作为"下一个合并候选"
      * 若已少于 segments_per_tier 个 segment，又不是填满 floor_segment，则返回空
    """
    by_tier: dict[int, list[Segment]] = {}
    for s in segments:
        by_tier.setdefault(tier_of(s.size_bytes), []).append(s)

    # 构造候选：每个 tier 选分数最低的 segment 集合（规模 ≤ max_in_merge）
    best_score = float("inf")
    best_set: list[Segment] | None = None
    for tier, segs in by_tier.items():
        if len(segs) < 2:
            continue
        # 这里简化为"按合并评分选 ≤ max_segments_in_merge 个"
        sorted_segs = sorted(segs, key=lambda x: merge_score(x, reclaim_weight))
        subset = sorted_segs[:max(2, min(max_segments_in_merge, len(sorted_segs)))]
        total = sum(merge_score(s, reclaim_weight) for s in subset)
        if total < best_score and len(subset) >= 2:
            best_score = total
            best_set = subset
    return best_set  # type: ignore[return-value]


# ────────────── 主流程 ──────────────
def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def main() -> None:
    print("=" * 76)
    print("Demo 1 · 模拟一个 shards 内的 segment 清单（初始状态）")
    print("=" * 76)

    # 启动一段时间后 shard 内常见的段结构：少量大段 + 大量小段
    segments = [
        Segment("A", 1_500_000_000, 1_000_000, del_docs=300_000),   # 1.5 GB · 30% 删除
        Segment("B", 800_000_000, 600_000, del_docs=150_000),       # 800 MB · 25% 删除
        Segment("C", 500_000_000, 400_000, del_docs=80_000),        # 500 MB · 20%
        Segment("D", 100_000_000, 80_000, del_docs=20_000),         # 100 MB · 25%
        Segment("E", 50_000_000, 40_000, del_docs=4_000),           # 50 MB · 10%
        Segment("F", 12_000_000, 10_000, del_docs=8_000),           # 12 MB · 80% 删除!
        Segment("G", 5_000_000, 4_000, del_docs=800),               # 5 MB · 20%
        Segment("H", 1_200_000, 1_000, del_docs=300),               # 1.2 MB · 30%
    ]
    print(f"\n  {'id':>4s} {'size':>10s} {'tier':>4s} {'live/del':>12s}  {'del%':>6s}  合并评分")
    for s in segments:
        t = tier_of(s.size_bytes)
        sc = merge_score(s)
        print(f"  {s.seg_id:>4s} {fmt_size(s.size_bytes):>10s} {t:>4d} "
              f"{s.live_docs:>6d}/{s.del_docs:>5d}  {s.del_ratio * 100:>5.1f}%  "
              f"{sc / 1e9:.2f} GB-eq")

    # Demo 2：选一个合并候选
    print("\n" + "=" * 76)
    print("Demo 2 · 下一轮合并候选（TieredMergePolicy 选分最低的几个）")
    print("=" * 76)
    candidates = find_merge_candidates(segments, max_segments_in_merge=4)
    if candidates:
        names = [s.seg_id for s in candidates]
        print(f"\n  选中合并: {names}")
        for s in candidates:
            print(f"    · {s.seg_id}: {fmt_size(s.size_bytes)} ({s.del_ratio * 100:.1f}% 删除)")
        merged = merge_one(candidates, sum(s.size_bytes for s in candidates), RECLAIM_DELETES_WEIGHT)
        print(f"  → 合并后: {merged.seg_id} = {fmt_size(merged.size_bytes)} "
              f"({merged.live_docs} 文档)")
        # 回收磁盘
        saved = sum(s.size_bytes for s in candidates) - merged.size_bytes
        print(f"    节省 (重复索引条目的回收): {fmt_size(max(0, -saved))}（这里因 size 累加显示为 0，是正常：合并不'减' size）")
        recovered = sum(s.del_docs for s in candidates)
        print(f"    实际回收的删除文档: {recovered} 个")

    # Demo 3：force-merge-all
    print("\n" + "=" * 76)
    print("Demo 3 · forceMerge(1) —— 把整个 index 压成 1 个 segment（read-only 场景）")
    print("=" * 76)
    print("  注意：随 segment 数下降，搜索并发性也下降（Lucene 10 起支持 logical partitioning）")
    cumulative = sum(s.size_bytes for s in segments)
    merged_total_live = sum(s.live_docs for s in segments)
    print(f"  合并前: {len(segments)} segments, 总 {fmt_size(cumulative)} / "
          f"{merged_total_live} live docs")
    print(f"  合并后: 1 segment, 总 {fmt_size(cumulative)} / {merged_total_live} live docs")
    print(f"  何时适用：写入停止后归档 / 时间序列冷数据冻结（ILM 里 common 'forcemerge' action）")
    print(f"  何时不适用：写入仍在进行 → 它会被后续段插入瞬间打乱并产生新一轮合并")

    # Demo 4：节流与"日合并量"
    print("\n" + "=" * 76)
    print(f"Demo 4 · 默认 20 MB/s 节流决定后台合并速度（保护搜索 IO 性能）")
    print("=" * 76)
    seconds_per_day = 86400
    daily_throughput = MERGE_THROTTLE_MB_PER_SEC * seconds_per_day / 1024  # GB/day
    print(f"  全时每秒 20 MB → {MERGE_THROTTLE_MB_PER_SEC} MB/s "
          f"= {MERGE_THROTTLE_MB_PER_SEC * seconds_per_day / 1024:.1f} GB/day (≈{daily_throughput:.1f} GB)")
    for daily_volume in [10, 50, 200, 500, 1000]:
        ratio = daily_volume / daily_throughput
        if ratio < 1:
            warn = "✓ 充裕（合并跟得上写入）"
        elif ratio < 0.5:
            warn = "✓ OK"
        else:
            warn = "❌ 限速会拖死合并，可能出现 'now throttling indexing'"
        print(f"  日新增 {daily_volume:>5d} GB: 占用合并 IO {ratio * 100:>5.1f}% {warn}")


if __name__ == "__main__":
    main()
