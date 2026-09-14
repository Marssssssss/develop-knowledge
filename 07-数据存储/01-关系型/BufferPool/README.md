# 缓冲池 Buffer Pool(InnoDB 风格 Midpoint LRU + Clock Sweep)

> 关系型数据库最核心的缓存层:**内存页缓存 + 替换算法 + 脏页调度 + WAL-before-data**。

## 一、简介

Buffer Pool 是数据库在内存中缓存磁盘页(默认 16 KB)的区域。所有 SQL 读写最终都落在这里。其核心问题:**替换算法**——LRU 是默认直觉,但"顺序扫描一次大量冷页"会让 LRU 把热数据全部冲掉,**热冷分离 LRU** 是 InnoDB 的解法。本 demo 实现 MySQL 8.0 官方手册 §17.5.1 的 midpoint insertion 策略:old sublist 占 3/8(= innodb_old_blocks_pct=37),新页插入到 midpoint,**必须在 old 区停留 innodb_old_blocks_time 毫秒 + 被再次访问**才晋升到 new 区(热数据区)。

我们还实现 WAL-before-data 守卫(脏页 LSN > 已刷 WAL 时禁止写回)+ Clock Sweep 淘汰(基于使用计数,O(1) 指针递增)+ Fuzzy Checkpoint(标记 + 异步刷脏)。

## 二、原理详解

### 2.1 Page Table → Frame

```
            page_id  ─→  page_table[hash]  ─→  frame_index  ─→  Frame
                                            │
                                            ├─ pin_count      (FIX/UNFIX 计数)
                                            ├─ dirty          (是否需要刷回)
                                            ├─ usage_count    (clock sweep)
                                            └─ is_old         (所在子表)
```

**FIX-UNFIX 协议**:上层使用页前调用 `FIX`,使用完 `UNFIX`。FIX 后 pin_count++ 阻止换出;UNFIX 后允许换出。这是 InnoDB MTR(Mini-Transaction)与 buffer pool 的接口。

### 2.2 Midpoint LRU

MySQL 8.0 manual §17.5.1(原文):

> "3/8 of the buffer pool is devoted to the old sublist."
> "When InnoDB reads a page into the buffer pool, it initially inserts it at the midpoint (the head of the old sublist)."
> "Accessing a page in the old sublist makes it 'young', moving it to the head of the new sublist."

逻辑结构:

```
LRU 全链:
[ new sublist 5/8 ........... | old sublist 3/8 ]
 MRU ─────────────────────────→ LRU
                                ↑ midpoint 插入点
```

新页总落在 midpoint**(永不直接到 MRU 头)**,目的是让**频繁访问的页通过"在 old 区再次被访问"自然晋升**,而一次性扫描页永远在 old 区老化淘汰。这能防御两大事务现象:

- **预读失效**(read-ahead 加载的页若后续不被访问 → 自然老化)
- **全表扫描污染**(一次扫描的页只访问一次 → 不会晋升到 new 区)

### 2.3 Clock Sweep 淘汰

实现细节(对比纯 LRU 的 O(1) per op):

```
sweep_pointer → frame i
  if frame.pin_count > 0: skip
  if frame.usage_count > 0:
     frame.usage_count -= 1   (给"第二次机会")
     advance_pointer
  else:
     return frame i            (victim)
```

比 LRU 链表节点移动便宜。

### 2.4 WAL-before-data 与 Checkpoint

每次 page 修改:写 WAL 记录到 log file → 标记 page.dirty = true → 给 page 打上最新 LSN。

刷脏页时:

```
if page.lsn > wal.flushed_lsn:
    error "WAL-before-data violated"
flush page to disk
```

**Fuzzy Checkpoint**:周期性记录当前 LSN,把 LSN ≤ 标记的脏页全部 flush,**不阻塞写入**。崩溃后从该 LSN 重放 redo log 即可。

### 2.5 PostgreSQL 对照

PG 用 **clock sweep + small ring buffers**(对大扫描单独分一块 256 KB ring,防止污染主 LRU)+ full-page images(首次修改 dirty 时记整页 WAL,防 torn page)。不像 InnoDB 把 young/old 显式分区。

## 三、对比矩阵

| 数据库 | 替换算法 | 扫描防御 | 双写/全页保护 |
| --- | --- | --- | --- |
| InnoDB | Midpoint LRU + clock sweep | old sublist + innodb_old_blocks_time | Doublewrite Buffer |
| PostgreSQL | Clock sweep + ring buffers | small ring buffer for bulk reads | full-page images(8 KB) |
| Oracle | Touch-count + on-demand | Multiple buffer pools | first-touch redo |

## 四、运行方式

```bash
cd 07-数据存储/01-关系型/BufferPool/
python buffer_pool.py
# 输出:
#   after scan: hits=0 misses=21 evictions=5
#   after hot loop: hits=15 misses=21 evictions=5 dirty=2
#   hot pages 1..5 still resident after scan? True

gcc -std=c11 buffer_pool.c -o buffer_pool && ./buffer_pool
go run buffer_pool.go
```

## 五、关键代码

`buffer_pool.py` 的核心:

```python
def _acquire_frame(self) -> int:
    if self.free:
        return self.free.popleft()
    # clock-sweep over old sublist
    candidates = self.lru[self._mru_index():]
    for fi in reversed(candidates):
        f = self.frames[fi]
        if f.pin_count == 0:
            if f.usage > 1:
                f.usage -= 1    # second chance
                continue
            return fi
```

`_lru_insert_old` 在 midpoint(`size * 63 / 100`)插入新页——这是 InnoDB 默认 5/8 new + 3/8 old 的来源。

## 六、性能边界

- **命中率**:典型 OLTP 工作集命中 ≥ 95%;全表扫描会让命中率临时跌至 50% 以下。
- **Clock Sweep 时间复杂度**:最坏 O(N)(所有 frame usage > 0);平均 O(1)(单次指针推进)。
- **WAL 刷盘延迟**:同步刷盘 fsync 1~10 ms;组提交可优化。
- **Checkpoint**:每 ~5 min 一次,恢复时间 ≤ checkpoint 间隔的 redo 量。

## 七、注意事项与常见坑

1. **innodb_old_blocks_time 单位**:是毫秒(默认 1000);调大可以挡住批量扫描,但也增加真热数据的延迟。
2. **innodb_old_blocks_pct**:调小(20~30)会让 new 区变大,适合 OLTP;调大(50)适合 OLAP 批量。
3. **Buffer Pool Instance**:高并发下拆分多个 instance 减少 mutex 争用,5.7 起默认 ≥ 1GB 时 8 个 instance。
4. **Page Cleaner 线程**:MySQL 5.6 引入,5.7 多线程并行刷脏,避免用户线程陷入 single-page flush。
5. **Doublewrite Buffer / Full-Page Image**:OS 4 KB 扇区下,16 KB 页写入可能 torn;InnoDB 写双份(2 MB 双写区 + 数据文件),PG 在首次修改时把整页写进 WAL。

## 八、参考资料

实际读过的权威链接:

1. MySQL 8.0 Reference Manual §17.5.1 Buffer Pool:https://dev.mysql.com/doc/refman/8.0/en/innodb-buffer-pool.html  *(3/8 old sublist + midpoint insertion 原文)*
2. 阿里云 RDS 团队 "InnoDB Buffer Pool flush 策略漫谈":https://www.bookstack.cn/read/aliyun-rds-core/f67b42b8f7e9ac29.md  *(Page Cleaner 模型 + hazard pointer + 5.7 优化细节)*
3. 庖丁解 InnoDB 之 Buffer Pool (catkang 2023):https://catkang.github.io/2023/08/08/mysql-buffer-pool.html  *(buf_page_get_gen + MTR + buf_block_t 结构)*
4. PostgreSQL Internals — Buffer Pool (Buffer Management):https://www.postgresql.org/docs/current/storage.html  *(对照 PG 的 ring buffer + clock sweep)*
5. "Buffer pool architecture" — aicassindra.com:https://aicassindra.com/blogs/databases/db_buffer_pool.html  *(frame array + descriptor + LRU-K 与扫描抵抗综述)*
6. "击穿 MySQL 性能天花板:InnoDB Buffer Pool 全解":https://developer.aliyun.com/article/1724662  *(三个核心链表 + 调优参数详解)*