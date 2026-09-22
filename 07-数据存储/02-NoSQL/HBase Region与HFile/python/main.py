"""HBase region 分裂策略演示：阈值随 region 数三次方增长，以及抖动带来的分裂错峰。"""

from regions import (
    DEFAULT_MAX_FILE_SIZE,
    DEFAULT_MEMSTORE_FLUSH,
    MB,
    REGION_SPLIT_LIMIT,
    ConstantSizeSplitPolicy,
    IncreasingToUpperBoundSplitPolicy,
    can_write_hfile,
    constant_size_threshold,
    hfile_default_version,
    jitter_rate,
    split_sequence,
)


def demo():
    p = IncreasingToUpperBoundSplitPolicy()
    print("=== 1. 默认分裂策略 IncreasingToUpperBound ===")
    print("  memstore flush = %dMB -> initialSize = %dMB（2 倍）"
          % (DEFAULT_MEMSTORE_FLUSH // MB, p.initial_size // MB))
    print("  hbase.hregion.max.filesize = %dMB" % (DEFAULT_MAX_FILE_SIZE // MB))
    print("  阈值 = min(maxFileSize, initialSize × region 数^3)")
    for n in (0, 1, 2, 3, 4, 10, 100, 101):
        print("    region 数 %3d -> 阈值 %8dMB"
              % (n, p.size_to_check(n) // MB))

    print()
    print("=== 2. 一张表的分裂过程（每轮 region 数 +1）===")
    for count, threshold in split_sequence(p, start_regions=1, rounds=6):
        print("  %d 个 region 时，超过 %8dMB 就分裂" % (count, threshold // MB))

    print()
    print("=== 3. 与 ConstantSize 策略的差别 ===")
    c = ConstantSizeSplitPolicy()
    print("  ConstantSize          : 恒为 %dMB（不受 region 数影响）" % (c.threshold // MB))
    print("  IncreasingToUpperBound: 单 region 时只要 %dMB，是它的 1/40"
          % (p.size_to_check(1) // MB))

    print()
    print("=== 4. jitter：让 region 不要同时分裂 ===")
    print("  jitterRate = (random - 0.5) × 0.25，落在 [-0.125, +0.125]")
    for r in (0.0, 0.25, 0.5, 0.75, 1.0):
        print("    random=%.2f -> rate=%+.4f -> 阈值 %dMB"
              % (r, jitter_rate(r), constant_size_threshold(DEFAULT_MAX_FILE_SIZE, r) // MB))
    lo = constant_size_threshold(DEFAULT_MAX_FILE_SIZE, 0.0) // MB
    hi = constant_size_threshold(DEFAULT_MAX_FILE_SIZE, 1.0) // MB
    print("  同样配置下两个 region 的实际阈值可能差 %dMB -> 错峰分裂" % (hi - lo))

    print()
    print("=== 5. regionSplitLimit 只是参考值 ===")
    print("  默认 %d；本模型在达到后停止分裂（官方说它不是硬上限）" % REGION_SPLIT_LIMIT)

    print()
    print("=== 6. HFile 版本 ===")
    print("  默认 hfile.format.version = %d（trailer 用 protobuf 序列化）" % hfile_default_version())
    for v in (2, 3, 4):
        print("    v%d 可写 = %s" % (v, can_write_hfile(v)))
    print("  v2 仍可读，但已不能写（升级前必须确认配置不是 2）")


if __name__ == "__main__":
    demo()
