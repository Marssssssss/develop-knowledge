"""VTGate 的路由决策模型。

文档原话: "Vitess calculates the sharding key or keys for each query and then
routes that query to the appropriate shards. ... a query that retrieves
information about several products might be directed to one or more shards"。

路由结果只有三种形态:

| 形态 | 触发条件 | fan-out |
| --- | --- | --- |
| 单分片(unique vindex 等值) | Primary Vindex 是 unique 且给了具体值 | 1 |
| 键范围(Sequential vindex 的 BETWEEN) | vindex 实现 `RangeMap`(numeric / binary) | 与区间宽度有关 |
| scatter(全分片) | 无分片键条件, 或区间无法映射(如 reverse_bits) | N |

`fan-out` 是分片代价的核心: 单分片查询可以下推 LIMIT/聚合/连接, scatter 必须在
VTGate 汇总, 且 N 个分片里最慢的那个决定了延迟。
"""

from keyranges import parse_shard_name, canonical
from vindexes import get as get_vindex


def route_equal(vindex_name, ids, shards):
    """等值查询: 每个 id 经 vindex 映射成 keyspace id, 再定位分片。"""
    vi = get_vindex(vindex_name)
    targets = {}
    for i in ids:
        ksid = vi.hash(i)
        hits = [s for s in shards if parse_shard_name(s).contains(ksid)]
        if len(hits) != 1:
            raise AssertionError("分区不完整或重叠: %r -> %r" % (i, hits))
        targets.setdefault(hits[0], []).append(i)
    return {
        "kind": "unique" if len(targets) == 1 else "multi",
        "shards": targets,
        "fanout": len(targets),
    }


def route_range(vindex_name, lo, hi, shards):
    """范围查询: 只有 Sequential vindex 能映射成 key range, 否则退化成 scatter。"""
    vi = get_vindex(vindex_name)
    if not vi.sequential:
        return {"kind": "scatter", "shards": {s: [] for s in shards},
                "fanout": len(shards), "reason": "vindex 未实现 RangeMap"}
    lo_k = _as_int(canonical(vi.hash(lo)))
    hi_k = _as_int(canonical(vi.hash(hi)))
    if lo_k > hi_k:
        lo_k, hi_k = hi_k, lo_k
    hits = []
    for s in shards:
        kr = parse_shard_name(s)
        s_lo = _as_int(canonical(kr.start)) if kr.start else 0
        s_hi = _as_int(canonical(kr.end)) if kr.end else (1 << 64)
        if max(lo_k, s_lo) < min(hi_k, s_hi):  # 半开区间相交: [lo,hi) ∩ [s_lo,s_hi)
            hits.append(s)
    return {"kind": "keyrange", "shards": {s: [] for s in hits}, "fanout": len(hits)}


def _as_int(b):
    return int.from_bytes(b, "big")


def route_unconstrained(shards):
    """不带分片键: 打到所有分片(scatter)。"""
    return {"kind": "scatter", "shards": {s: [] for s in shards}, "fanout": len(shards)}


def reshard(shards, target):
    """把 target 分片对半切, 返回新分区; 用于验证"键不动、只有分片边界动"。"""
    from keyranges import split_shard

    if target not in shards:
        raise ValueError("no such shard: %s" % target)
    kids = split_shard(target)
    out = []
    for s in shards:
        out.extend(split_shard(s) if s == target else [s])
    return out, kids
