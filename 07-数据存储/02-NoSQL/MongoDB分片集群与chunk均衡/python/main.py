"""MongoDB 分片集群：chunk 分裂、均衡阈值判定与 jumbo 诊断。"""

from chunking import (
    DEFAULT_CHUNK_MB,
    Chunk,
    Migration,
    balance_round,
    max_parallel_migrations,
    migration_threshold,
    needs_balancing,
    split_plan,
)


def demo():
    print("=== 1. 均衡阈值：3 × range size ===")
    print("  默认 range size = %dMB -> 阈值 %dMB" % (DEFAULT_CHUNK_MB, migration_threshold()))
    for sizes in ([1000, 600], [1000, 616], [1000, 617], [900, 800, 700]):
        print("  %-22s 差 %4dMB -> %s"
              % (sizes, max(sizes) - min(sizes),
                 "触发迁移" if needs_balancing(sizes) else "视为均衡"))

    print()
    print("=== 2. 并发迁移数 = floor(n/2) ===")
    for n in range(1, 9):
        print("  %d 个 shard -> 最多 %d 个并发迁移" % (n, max_parallel_migrations(n)))

    print()
    print("=== 3. 自动分裂（二分到不超过 range size）===")
    for size in (129, 256, 900, 5000):
        splits, parts, each = split_plan(size)
        print("  %5dMB -> 分裂 %d 次，%2d 块 × %.1fMB" % (size, splits, parts, each))

    print()
    print("=== 4. jumbo chunk 诊断 ===")
    for chunk in (Chunk("a", "b", 200, 1),
                  Chunk("a", "b", 200, 5),
                  Chunk("2026-09-22", "2026-09-23", 500, 1)):
        chunk.evaluate()
        print("  %s" % chunk)
        print("     -> %s" % ("不可分裂，需 refine shard key 或 reshard"
                              if chunk.jumbo else "可 splitAt，均衡器能处理"))

    print()
    print("=== 5. 迁移状态机（异步删除）===")
    m = Migration("chunk-1")
    print("  步骤数 = %d" % len(Migration.STEPS))
    for i in range(8):
        m.advance()
        print("  advance %d -> step=%d done=%s deleted=%s"
              % (i + 1, m.step, m.done, m.deleted))
        if m.deleted:
            break

    print()
    print("=== 6. 一轮均衡过程（3 shard）===")
    rounds, moves = balance_round([1000, 600, 600])
    for i, r in enumerate(rounds):
        print("  第 %d 次迁移后: %s (差 %dMB)" % (i + 1, r, max(r) - min(r)))
    print("  共 %d 次迁移，最终差值 %dMB（阈值 %dMB）"
          % (moves, max(rounds[-1]) - min(rounds[-1]), migration_threshold()))


if __name__ == "__main__":
    demo()
