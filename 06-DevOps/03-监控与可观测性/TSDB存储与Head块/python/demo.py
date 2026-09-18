"""Prometheus TSDB 存储模型 —— 可实跑自检。

运行：``python demo.py``（纯标准库，退出码 0 = 全部断言通过）

覆盖 7 组：
  A ULID 块命名与字典序    B 2h 块布局与 chunk 段文件
  C 压缩计划与磁盘峰值      D 容量估算（保留期 / 样本率 / 每样本字节）
  E WAL 段、checkpoint 与并行重放
  F 保留策略（时间 vs 容量、整块过期）与备份丢失窗口
  G 倒排索引与 merge-join
"""

import sys

from tsdb_model import (BLOCK_DURATION, CHUNK_SEGMENT_SIZE, DEFAULT_RETENTION,
                        MAX_COMPACTED_SPAN, MIN_WAL_SEGMENTS, UNSUPPORTED_FS,
                        WAL_SEGMENT_SIZE, Block, InvertedIndex,
                        backup_loss_window, block_bounds, compaction_ladder,
                        max_compacted_span, needed_disk_space,
                        peak_bytes_during_compaction, pick_compaction,
                        replay_range, series_reduction_vs_interval, ulid_decode,
                        ulid_encode, wal_segments_for, wal_shards,
                        which_retention_fires)

PASS = 0
FAIL = []


def check(label, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append("%s | %s" % (label, detail))
        print("FAIL  %s | %s" % (label, detail))


def approx(a, b, tol=1e-9):
    return abs(a - b) < tol


# ------------------------------------------------------------ A ULID
def group_a():
    ts = 1625000000000
    u = ulid_encode(ts, 0)
    check("A ULID 为 26 字符 Crockford Base32", len(u) == 26, u)
    back_ts, back_rand = ulid_decode(u)
    check("A ULID 编解码往返一致（时间戳与随机数）",
          back_ts == ts and back_rand == 0, (back_ts, back_rand))
    check("A ULID 首字符只能是 0-7（128 位值只占满 26*5=130 位的高 2 位留空）",
          ulid_encode((1 << 48) - 1, (1 << 80) - 1)[0] == "7",
          ulid_encode((1 << 48) - 1, (1 << 80) - 1))
    ts_list = [1625000000000, 1625000000001, 1625000003600, 1625000007000]
    ulids = [ulid_encode(t, 0) for t in ts_list]
    check("A ULID 字典序 == 时间序（块名可直接按名排序）",
          ulids == sorted(ulids), ulids)
    check("A 同一毫秒内靠 80 位随机数破解并列",
          ulid_encode(ts, 0) < ulid_encode(ts, 1))
    check("A 非法输入被拒绝",
          _raises(lambda: ulid_encode(1 << 48, 0))
          and _raises(lambda: ulid_decode("short")))


def _raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


# --------------------------------------------------- B 块布局与段文件
def group_b():
    start, end = block_bounds(0)
    check("B 块时间窗为 2 小时（半开区间）", end - start == BLOCK_DURATION == 7200,
          end - start)
    big = Block(ulid_encode(1, 0), 0, num_samples=int(1e9 / 1.5), num_series=1000)
    check("B 1GB 级块切成 2 个 512MB chunk 段", big.chunk_segments() == 2,
          big.chunk_segments())
    tiny = Block(ulid_encode(1, 0), 0, num_samples=1000, num_series=10)
    check("B 小块只需 1 个段", tiny.chunk_segments() == 1)
    check("B chunk 段上限 512MB / WAL 段上限 128MB（官方默认）",
          CHUNK_SEGMENT_SIZE == 512 * 1024 * 1024 and WAL_SEGMENT_SIZE == 128 * 1024 * 1024)
    check("B 默认保留期 15 天", DEFAULT_RETENTION == 15 * 86400)


# ------------------------------------------------------ C 压缩计划
def group_c():
    d15 = max_compacted_span(15 * 86400)
    check("C 保留 15d → 压缩后块跨度上限 = 10% × 15d = 36h（< 31d）",
          approx(d15, 36 * 3600), d15)
    check("C 保留 90d → 上限 = 9d（仍 < 31d）",
          approx(max_compacted_span(90 * 86400), 9 * 86400))
    check("C 保留 365d → 10% 为 36.5d 超顶，取 31d",
          approx(max_compacted_span(365 * 86400), MAX_COMPACTED_SPAN)
          and MAX_COMPACTED_SPAN == 31 * 86400)
    ladder = compaction_ladder(15 * 86400)
    check("C 15d 保留下压缩阶梯 = 2/4/8/16/32h 共 5 级",
          ladder == [7200, 14400, 28800, 57600, 115200], ladder)
    check("C 阶梯末级不超过上限且下一级会超出",
          ladder[-1] <= d15 < ladder[-1] * 2, (ladder[-1], d15))
    check("C 365d 保留下阶梯到 9 级（末级 21.33d，仍达不到 31d 顶）",
          len(compaction_ladder(365 * 86400)) == 9,
          len(compaction_ladder(365 * 86400)))

    blocks = [Block(ulid_encode(1000 + i, 0), i * 7200, 72000, 100) for i in range(5)]
    lvl, sel = pick_compaction(blocks, 15 * 86400)
    check("C 同层 5 个 2h 块总跨度 10h < 36h 上限 → 全部入选压缩", len(sel) == 5, len(sel))
    check("C 压缩产物进入下一层（level+1）", lvl == 1, lvl)
    lvl2, sel2 = pick_compaction(blocks, 4 * 3600)
    check("C 边界：保留期 4h → 上限 0.4h < 基础块 2h，任何组合都压不动",
          sel2 == [] and lvl2 is None, (lvl2, len(sel2)))
    src = [b.size_bytes for b in blocks]
    check("C 压缩期间源块与新块共存 → 峰值 = 2 × 源块总量",
          approx(peak_bytes_during_compaction(src), 2 * sum(src)))


# ------------------------------------------------------ D 容量估算
def group_d():
    s15 = 15 * 86400
    check("D 保留 15d / 10 万样本每秒 / 1.5B 每样本 → 194.4 GB",
          approx(needed_disk_space(s15, 100000, 1.5), 1.944e11, 1e6),
          needed_disk_space(s15, 100000, 1.5))
    check("D 每样本 1B（官方下界）→ 129.6 GB",
          approx(needed_disk_space(s15, 100000, 1.0), 1.296e11, 1e6))
    check("D 每样本 2B（官方上界）→ 259.2 GB",
          approx(needed_disk_space(s15, 100000, 2.0), 2.592e11, 1e6))
    check("D 官方「每样本 1~2 字节」的上下界比恰为 2",
          approx(needed_disk_space(s15, 1, 2.0) / needed_disk_space(s15, 1, 1.0), 2.0))

    n, overhead, per_series, bps = 1000, 256, 86400, 1.5
    a, b = series_reduction_vs_interval(n, overhead, per_series, bps)
    check("D 压缩率与序列长度无关时，序列减半恰好省下「一半的固定开销」",
          approx(b - a, (n / 2) * overhead), b - a)
    check("D 该差额 = 128000 字节（1000 序列 × 256B 开销的一半）",
          approx(b - a, 128000.0))
    a2, b2 = series_reduction_vs_interval(n, overhead, per_series, bps, 1.6)
    check("D 压缩率随序列内样本数下降时（1.5→1.6B），间隔翻倍的优势被吃掉",
          b2 - a2 > b - a and approx(a2, a),
          (a2, b2))
    check("D 后者比前者多花 4.45 MB（0.5 × 86400 × 0.1B × 1000 序列）",
          approx((b2 - a2) - (b - a), 0.5 * per_series * 0.1 * n, 1e3),
          (b2 - a2) - (b - a))
    disk = float(1 << 40)  # 1 TiB 分配盘
    for frac in (0.80, 0.85):
        rs = frac * disk
        check("D retention.size = %.2f × 盘 → 缓冲恰为 %.0f%%（官方建议 15~20%%）"
              % (frac, (disk - rs) / disk * 100),
              0.15 <= (disk - rs) / disk <= 0.20, (frac, (disk - rs) / disk))


# ------------------------------------------------------ E WAL
def group_e():
    check("E WAL 至少保留 3 段（每段 128MB → 384 MiB 下限）",
          MIN_WAL_SEGMENTS == 3 and 3 * WAL_SEGMENT_SIZE == 384 * 1024 * 1024)
    n = wal_segments_for(2 * 3600, 100000, 2.0)
    check("E 2h 原始数据（10 万样本/s、WAL 每样本 2B）→ 11 段", n == 11, n)
    check("E 空负载仍回落到 3 段下限", wal_segments_for(0, 0, 2.0) == 3)
    check("E 1h 数据 → 6 段（段数随时间线性增长）",
          wal_segments_for(3600, 100000, 2.0) == 6,
          wal_segments_for(3600, 100000, 2.0))

    shards = wal_shards(list(range(10)), 4)
    check("E 重放按 ref % workers 分片：4 个 worker 分别拿 3/3/2/2 条",
          [len(s) for s in shards] == [3, 3, 2, 2], [len(s) for s in shards])
    flat = sorted(r for s in shards for r in s)
    check("E 分片是划分：并集恰好覆盖全部 ref 且不重不漏", flat == list(range(10)), flat)
    check("E 全量重放与分片重放的归属一致（ref=7 → worker 3）", 7 in shards[3])

    check("E 有 checkpoint 时只重放其后的段",
          replay_range([2], [1, 2, 3, 4, 5]) == [3, 4, 5])
    check("E 取最新 checkpoint",
          replay_range([1, 2], [1, 2, 3, 4]) == [3, 4])
    check("E 无 checkpoint 时重放全部段", replay_range([], [1, 2, 3]) == [1, 2, 3])


# ------------------------------------------- F 保留策略与备份窗口
def group_f():
    check("F 保留 15d、按 1MB/s 增长 → 100GB 上限先触发（约 27.8h < 15d）",
          which_retention_fires(15 * 86400, 0, 1e6, 100e9) == "size")
    check("F 保留 15d、按 10KB/s 增长 → 时间先触发（115.7d > 15d）",
          which_retention_fires(15 * 86400, 0, 1e4, 100e9) == "time")
    check("F 只配时间保留 → time",
          which_retention_fires(15 * 86400, 0, 1e6, 0) == "time")
    check("F 容量已达上限 → 立刻走 size",
          which_retention_fires(15 * 86400, 200e9, 1e6, 100e9) == "size")

    now, retention = 30000.0, 3600.0
    cutoff = now - retention  # 26400
    check("F 块必须**整块过期**才删除（末样本早于 cutoff 才算）",
          Block("x", 18000, 1, 1).is_expired(now, retention)
          and not Block("x", 20000, 1, 1).is_expired(now, retention),
          (Block("x", 18000, 1, 1).end, Block("x", 20000, 1, 1).end, cutoff))
    span = Block("x", 20000, 1, 1)
    check("F 块起点 20000 虽早于 cutoff 26400，但末样本 27200 未过期 → 仍保留",
          span.start < cutoff < span.end and not span.is_expired(now, retention),
          (span.start, cutoff, span.end))

    blocks = [Block(ulid_encode(1000 + i, 0), i * 7200, 72000, 100) for i in range(3)]
    lo, hi = backup_loss_window(blocks)
    check("F 排除 wal/ 后丢失窗口从「最后一个已落盘块的末样本」开始",
          approx(lo, max(b.end for b in blocks)), lo)
    check("F 丢失窗口跨度 = head 持有的 2h", approx(hi - lo, 2 * 3600), hi - lo)
    check("F 官方表述为「覆盖最近 3 小时」→ 严格大于朴素的 2h head 口径（把落盘滞后计入）",
          3 * 3600 > (hi - lo), (hi - lo, 3 * 3600))
    check("F 非 POSIX 文件系统（NFS/EFS）不受支持", UNSUPPORTED_FS == {"nfs", "efs", "smb", "cifs"})


# ------------------------------------------------------ G 倒排索引
def group_g():
    idx = InvertedIndex()
    idx.add(1, {"method": "GET", "handler": "/api"})
    idx.add(2, {"handler": "/api"})
    idx.add(5, {"method": "GET", "handler": "/api"})
    idx.add(8, {"handler": "/api"})
    for ref in (12, 47, 103):
        idx.add(ref, {"method": "GET"})
    check("G posting list 升序（merge-join 的前提）",
          idx.postings[("method", "GET")] == [1, 5, 12, 47, 103],
          idx.postings[("method", "GET")])
    check("G 单标签 posting 与官方示例一致",
          idx.postings[("handler", "/api")] == [1, 2, 5, 8],
          idx.postings[("handler", "/api")])
    refs, comps = idx.intersect([("method", "GET"), ("handler", "/api")])
    check("G 交集 = [1, 5]", refs == [1, 5], refs)
    check("G merge-join 比较次数 4 ≤ n+m = 9（有序表线性合并）",
          comps == 4 and comps <= 5 + 4, comps)
    refs2, _ = idx.intersect([("method", "POST")])
    check("G 不存在的标签值 → 空集且不报错", refs2 == [])
    refs3, _ = idx.intersect([])
    check("G 无 matcher → 全量 ref", refs3 == [1, 2, 5, 8, 12, 47, 103], refs3)
    check("G 交集为空时提前退出（不再比较后续列表）",
          idx.intersect([("method", "POST"), ("handler", "/api")])[1] == 0)


def main():
    for g in (group_a, group_b, group_c, group_d, group_e, group_f, group_g):
        g()
    print("-" * 60)
    if FAIL:
        print("断言失败 %d 项 / 通过 %d 项" % (len(FAIL), PASS))
        return 1
    print("全部 %d 项断言通过" % PASS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
