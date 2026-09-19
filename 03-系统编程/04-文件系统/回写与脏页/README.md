# 回写与脏页：dirty_* 到底在调什么

## 简介

`write()` 返回了，数据**没有**落到磁盘 —— 它只是把 page cache 里的页弄"脏"了。
真正把它写出去的是内核的 flusher 线程，或者（在极端情况下）是**你这个写进程自己**。

这套机制的全部旋钮都在 `/proc/sys/vm/` 里，而它们之间的关系比看上去绕：
两个阈值、两个互为替代的单位、两个时间参数，外加一个"分母不是你想的那个"的陷阱。

本 demo 用 **Python（34 项断言，可实跑）+ Go** 把这套规则做成可执行的记账模型。

## 原理详解

### 1. 分母是 available memory，不是总内存

文档对 `dirty_ratio` / `dirty_background_ratio` 的说明都带着同一句话：

> The total available memory is not equal to total system memory.

`available = free pages + reclaimable pages`。demo 里 8 GiB 的机器、6 GiB free、
1 GiB reclaimable，available 就是 **7 GiB** —— 20% 的阈值是 1.4 GiB 而不是 1.6 GiB，
差 12.5%。内存被大量占用（reclaimable 少）时，这个差别会更大。

### 2. 两个阈值，两种后果

| 阈值 | 谁被触发 | 后果 |
| --- | --- | --- |
| `dirty_background_*` | 后台 flusher 线程 | **异步**写回，应用不感知 |
| `dirty_*` | **正在写的那个进程** | 同步写回，write 被拖慢（限流） |

正常配置下 background 必须**小于** dirty，否则限流会先于后台回写发生，
后台阈值形同虚设（demo 有这条反例断言）。

### 3. `*_bytes` 与 `*_ratio` 只能活一个

文档原文：*Only one of them may be specified at a time. When one sysctl is written
it is immediately taken into account to evaluate the dirty memory limits and the
other appears as 0 when read.*

也就是说：你写 `vm.dirty_bytes`，回头读 `vm.dirty_ratio` 会得到 **0** ——
**0 不代表"没启用"，而是"当前由 bytes 说了算"**。这是调参时最大的误读来源。

另外 `dirty_bytes` 有硬下限：文档写明 *the minimum value allowed for dirty_bytes is
two pages (in bytes); any value lower than this limit will be ignored and the old
configuration will be retained*。写小了**静默失败**，配置原封不动。

### 4. 两个时间参数，单位都是 1/100 秒

| 参数 | 管什么 |
| --- | --- |
| `dirty_expire_centisecs` | 脏多久算"过期"，过期的数据下一轮 flusher 醒来就写 |
| `dirty_writeback_centisecs` | flusher 多久醒一次 |

两个是**乘性关系**：`dirty_expire_centisecs` 远大于 `dirty_writeback_centisecs` 时，
实际写回时刻会被量化到唤醒点（demo 里 30 秒过期、5 秒唤醒 → 实际在 35 秒那轮写掉）。

**`dirty_writeback_centisecs = 0` 会完全禁用定期回写**（文档：*Setting this to zero
disables periodic writeback altogether*）—— 这时只剩"越过阈值"和"显式 sync"两条路。

`dirtytime_expire_seconds` 是给 lazytime inode 用的（只因 atime 变脏的 inode）；
置 0 同样表示禁用它的定期回写。

### 5. drop_caches 不碰脏页

文档：drop_caches 让内核丢掉**干净的**缓存以及可回收的 slab（dentry、inode）。
所以 `echo 3 > /proc/sys/vm/drop_caches` **不会**帮你把脏数据落盘；
而且它只是把 reclaimable 转成 free，**available 总量不变，阈值也就不动**。

## 对比

| | 后台回写 | 写者限流 | 显式 sync |
| --- | --- | --- | --- |
| 触发条件 | 脏页 ≥ background 阈值 / 数据过期 | 脏页 ≥ dirty 阈值 | `fsync` / `syncfs` / `O_SYNC` |
| 谁付出代价 | 内核线程 | **应用自己** | 应用自己 |
| 对延迟的影响 | 无 | write 尾延迟飙升 | 显式且可预期 |
| 典型阈值 | 10% available | 20% available | — |

## 环境

- Python 3.8+（自检零依赖）
- Go 1.20+（本机无工具链时走代码审查）

## 运行方式

```bash
cd python && python check.py     # 34 项断言
cd go && go run .
```

## 关键代码

阈值计算与 counterpart 互斥（Python 节选）：

```python
def background_thresh(self):
    if self.vm.dirty_background_bytes:
        return self.vm.dirty_background_bytes
    return self.available_bytes() * self.vm.dirty_background_ratio // 100

def write(self, name, value):
    if name == "dirty_bytes":
        if value != 0 and value < 2 * PAGE:
            return False                 # 低于两页：静默忽略
        self.dirty_bytes = value
        self.dirty_ratio = 0             # counterpart 读出来是 0
        return True
```

flusher 一轮的动作（先过期、后阈值）：

```python
def flusher_wakeup(self):
    flushed, keep = [], []
    for chunk, age in self.dirty:
        if age >= self.vm.dirty_expire_centisecs:
            flushed.append(chunk); self.written += chunk
        else:
            keep.append([chunk, age])
    self.dirty = keep
    if self.dirty_bytes() >= self.background_thresh():
        before = self.dirty_bytes()
        self._flush_until_below(self.background_thresh())
        flushed.append(before - self.dirty_bytes())
    return flushed
```

## 性能边界

- **调大阈值 = 攒更多脏页 = 更好的顺序写与合并**，代价是崩溃时丢更多数据、
  以及限流那一刻的延迟尖峰。
- **调小阈值**适合要求"数据尽快落盘"或写延迟稳定的场景，但会把大顺序写切碎。
- **后台与限流阈值之间的间隔**决定了"应用被拖慢之前有多少缓冲"：
  间隔太小 → 后台还没写完就被限流；太大 → 限流时一次性要写很多。
- `dirty_writeback_centisecs` 调小会**更频繁唤醒 flusher**，IO 更平滑但 CPU 与寻道更多。
- 写密集场景下真正的瓶颈往往是**设备**，不是阈值：阈值只决定"什么时候开始写"，
  不决定"能写多快"。

## 注意事项与常见坑

1. **`dirty_ratio` 读出 0 不代表没启用**，可能只是当前由 `dirty_bytes` 接管。
2. **`dirty_bytes` 写小了会被静默忽略**（下限两页），改完一定要读回来确认。
3. **分母是 available 不是 total**，按总内存估算阈值会偏大。
4. **`dirty_background_*` 必须小于 `dirty_*`**，否则限流抢在后台回写前面。
5. **`dirty_writeback_centisecs = 0` 是"关闭定期回写"**，不是"0 秒间隔"。
6. **`drop_caches` 不落脏页**，也不改变 available，别拿它当 `sync` 用。
7. **调大 dirty 阈值会让 `fsync` 变慢** —— 要落的数据变多了，这是常被忽略的副作用。
8. 在有**电池保护写缓存**的阵列上调参的风险要单独评估：设备层已经吸收了部分写。
9. 容器里看到的是**宿主机**的 sysctl（非特权容器通常不可写），调之前先确认命名空间。
10. 想观察效果就盯 `/proc/meminfo` 的 `Dirty` / `Writeback`，而不是只看 write 的返回。

## 参考资料

以下均为本 demo 撰写时**实际读取**的资料：

- Linux 内核文档《Documentation for /proc/sys/vm/》（docs.kernel.org）：
  <https://docs.kernel.org/admin-guide/sysctl/vm.html>
  （`dirty_background_bytes` / `dirty_background_ratio` / `dirty_bytes` /
  `dirty_expire_centisecs` / `dirty_ratio` / `dirtytime_expire_seconds` /
  `dirty_writeback_centisecs` / `drop_caches` 全部条目的原文措辞，
  包括 available memory 口径、counterpart 互斥、两页下限、置 0 即禁用）
