"""并行 worker 数量的官方公式。

逐行转写 `src/backend/optimizer/path/allpaths.c` 的
`compute_parallel_worker(rel, heap_pages, index_pages, max_workers)`:

```c
if (rel->rel_parallel_workers != -1)
    parallel_workers = rel->rel_parallel_workers;      // 表级 reloption 直接决定
else {
    if (rel->reloptkind == RELOPT_BASEREL &&
        ((heap_pages >= 0 && heap_pages < min_parallel_table_scan_size) ||
         (index_pages >= 0 && index_pages < min_parallel_index_scan_size)))
        return 0;                                       // 太小, 不值得并行
    if (heap_pages >= 0) {
        heap_parallel_threshold = Max(min_parallel_table_scan_size, 1);
        heap_parallel_workers = 1;
        while (heap_pages >= heap_parallel_threshold * 3) {
            heap_parallel_workers++;
            heap_parallel_threshold *= 3;
            ...
        }
        parallel_workers = heap_parallel_workers;
    }
    if (index_pages >= 0) { ...parallel_workers = Min(parallel_workers, index_parallel_workers); }
}
parallel_workers = Min(parallel_workers, max_workers);
```

配套 GUC 默认值(官方文档 runtime-config-query / resource):

| 参数 | 默认 |
| --- | --- |
| `min_parallel_table_scan_size` | 8MB = 1024 页(8kB/页) |
| `min_parallel_index_scan_size` | 512kB = 64 页 |
| `max_parallel_workers_per_gather` | 2 |
| `max_parallel_workers` | 8 |
| `parallel_setup_cost` | 1000 |
| `parallel_tuple_cost` | 0.1 |
| `parallel_leader_participation` | on |

注意最后那个 `Min(..., max_workers)`:公式算出来的是"想要几个",
`max_parallel_workers_per_gather` 才是上限;运行时还可能因为
`max_worker_processes` / `max_parallel_workers` 拿不到进程而**退化为 0**。
"""

BLCKSZ_PAGES = 8192            # 8kB/页
MIN_TABLE_SCAN_PAGES = 1024    # 8MB
MIN_INDEX_SCAN_PAGES = 64      # 512kB
DEFAULT_MAX_PER_GATHER = 2
PARALLEL_SETUP_COST = 1000.0
PARALLEL_TUPLE_COST = 0.1


def log3_workers(pages, min_pages):
    """源码里的 while 循环: 阈值三倍三倍地涨, 每翻一倍阈值就多一个 worker。"""
    if pages < 0:
        return None
    threshold = max(min_pages, 1)
    n = 1
    while pages >= threshold * 3:
        n += 1
        threshold *= 3
        if threshold > (2 ** 31 - 1) // 3:
            break                      # 源码的溢出保护
    return n


def compute_parallel_worker(heap_pages=-1, index_pages=-1,
                            max_workers=DEFAULT_MAX_PER_GATHER,
                            rel_parallel_workers=-1,
                            is_baserel=True,
                            min_table=MIN_TABLE_SCAN_PAGES,
                            min_index=MIN_INDEX_SCAN_PAGES):
    """返回 (workers, 说明)。"""
    if rel_parallel_workers != -1:
        w = min(rel_parallel_workers, max_workers)
        return w, "表级 reloption parallel_workers=%d 直接决定, 再被 max_workers 截断" % rel_parallel_workers
    if is_baserel and (
        (heap_pages >= 0 and heap_pages < min_table)
        or (index_pages >= 0 and index_pages < min_index)
    ):
        return 0, "BASEREL 且小于 min_parallel_*_scan_size, 直接返回 0"

    workers = 0
    if heap_pages >= 0:
        workers = log3_workers(heap_pages, min_table)
    if index_pages >= 0:
        idx = log3_workers(index_pages, min_index)
        workers = min(workers, idx) if workers > 0 else idx
    capped = min(workers, max_workers)
    note = "log3 公式给出 %d, 上限 max_workers=%d -> %d" % (workers, max_workers, capped)
    return capped, note


def parallel_plan_cost(serial_total, rows, workers, leader_participation=True):
    """并行计划的代价估算(官方代价模型)。

    `Gather` 之上: total = parallel_setup_cost + 并行部分 cost + rows * parallel_tuple_cost。
    leader 参与时并行部分由 (workers + 1) 个进程分摊。
    """
    if workers <= 0:
        return serial_total
    share = serial_total / (workers + 1) if leader_participation else serial_total / workers
    return PARALLEL_SETUP_COST + share + rows * PARALLEL_TUPLE_COST


def can_parallelize(writes_data=False, cursor_or_suspendable=False,
                    has_unsafe_function=False, nested_in_parallel=False,
                    max_per_gather=DEFAULT_MAX_PER_GATHER):
    """官方文档 15.2「何时不能用并行」的四条硬约束。"""
    reasons = []
    if max_per_gather <= 0:
        reasons.append("max_parallel_workers_per_gather <= 0")
    if writes_data:
        reasons.append("查询写数据或锁行(顶层或 CTE 里有数据修改)")
    if cursor_or_suspendable:
        reasons.append("查询可能被挂起(DECLARE CURSOR / PLpgSQL FOR 循环)")
    if has_unsafe_function:
        reasons.append("用到 PARALLEL UNSAFE 函数(用户自定义函数默认 UNSAFE)")
    if nested_in_parallel:
        reasons.append("已经在一个并行查询里(并行内不再并行)")
    return (len(reasons) == 0), reasons
