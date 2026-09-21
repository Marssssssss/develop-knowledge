# 标记-清除（mark-sweep）与空闲链表合并（coalescing）

> 目录：`03-系统编程/03-内存管理/垃圾回收/mark-sweep-free-list/`
> 语言：Python（`python/main.py` + `python/selfcheck_ms.py`，**60 断言实跑全绿**）/ Go（`go/main.go` 人工审查）

## 一、简介

把「垃圾怎么找出来」（mark-sweep）和「回收之后空闲内存怎么组织」（空闲链表 + 合并）这两件事放在一起看：
**回收算法只负责把不用的 chunk 标成空闲，真正决定下次能不能分配成功的是空闲链表的组织方式。**

本 demo 用 dlmalloc（Doug Lea allocator，即 glibc ptmalloc2 的祖先）的真实设计口径建模：

- **边界标记（Boundary Tags）**：chunk 前后各带尺寸信息，使「向前/向后遍历」与「和邻居合并」都能 O(1) 完成；
- **分箱（Binning）**：128 个近似对数间隔的 bin，< 512 B 的 bin 每箱只装一种尺寸（间隔 8 B）；
- **放置策略**：smallest-first / best-fit，bin 内按尺寸排序、同尺寸最旧优先；
- **wilderness preservation**、**mmap 阈值**、**延迟合并**三条启发式；
- 在此之上叠加 **mark-sweep**：分配失败 → 触发 GC → 重试。

## 二、原理

### 2.1 边界标记

```
 chunk:  [ size|flag ][   payload ...  ][ size(footer) ]
                                        ^ 只有空闲 chunk 必须有
```

- 头部在 chunk 起始处，尾部（trailer）在**下一个 chunk 的头部之前**；
- 空闲 chunk 的 footer 让「从当前 chunk 往前找到上一个 chunk 的尺寸」变成 O(1)，从而支持**向后合并**；
- dlmalloc 2.7 之后**省略使用中 chunk 的 trailer**（反正用不到），代价是错误检测能力变弱。

### 2.2 合并的三种时机

| 策略 | 行为 | 代价 / 收益 |
| --- | --- | --- |
| 立即合并 | `free()` 时就把左右邻居并进来 | 空闲块数最少，但 `free` 变慢且容易「合并—分割」抖动 |
| **延迟合并**（Deferred Coalescing） | 先挂在链表上，真正需要时才并 | `free` 快；但会出现**假性 OOM** |
| 不合并 | 永不并 | 碎片最快堆积 |

本 demo 用 `Heap(20, defer_coalesce=True)` 复现假性 OOM：三个 5-word 块都空闲（共 15 word 可用），但 9-word 的请求失败；强制 `coalesce()` 后同样请求成功。

### 2.3 放置策略对照

碎片布局为「空闲块 size = 20 / 5 / 5，请求 5 word」时：

| 策略 | 选中 | 依据 |
| --- | --- | --- |
| first-fit | 地址最低的（size 20） | 只看地址序 |
| best-fit | 最小的（size 5） | 只看尺寸最小 |
| dlmalloc（best + wilderness） | 非 wilderness 的那个 | wilderness 被当成「可以变得比谁都大」，只在没有其它候选时才用 |

对照组（非 wilderness 块 16 word、wilderness 8 word、请求 5 word）能看出 best-fit 与 dlmalloc 的分歧：
纯 best-fit 选 8-word 的 wilderness，dlmalloc 选 16-word 的普通块。

### 2.4 mark-sweep 与合并的耦合

- 标记阶段代价 **O(可达对象 + 引用边)**；清扫阶段必须**扫过整个堆**，代价 O(heap)；
- 清扫完之后相邻空闲块**必须**合并，否则刚回收的碎片直接变成下一次分配失败；
- 引用计数对环形垃圾**漏报**（本 demo 里 `a <-> b` 互引用时回收 0 个，mark-sweep 回收 2 个）。

## 三、对比

| 维度 | mark-sweep + 空闲链表 | 复制式（Cheney） | 引用计数 |
| --- | --- | --- | --- |
| 环形垃圾 | 可收 | 可收 | **漏报** |
| 空间开销 | 每 chunk 一个头（+ 空闲 chunk 的 footer 与两条链指针） | 2× 堆 | 每对象一个计数字段 |
| 是否移动对象 | 否（指针天然稳定） | 是 | 否 |
| 清扫代价 | O(heap)，与存活量无关 | O(存活量) | 0（释放即时） |
| 最小 chunk | 64 位 24 B / 32 位 16 B | 无此概念 | 无此概念 |

## 四、环境

- Python ≥ 3.9（仅标准库），Go 1.20+（Go 版本无工具链时走人工审查）
- 无需第三方依赖

## 五、运行

```bash
cd python && python main.py            # 基础演示：分配/释放/合并
cd python && python selfcheck_ms.py    # 60 条断言
cd go     && go run main.go            # Go 镜像（需 Go 工具链）
```

## 六、关键代码

```python
def coalesce(self):
    out = []
    for b in self.blocks:
        if out and out[-1].free and b.free \
           and out[-1].addr + out[-1].size == b.addr \
           and not (out[-1].mmapped or b.mmapped):
            out[-1].size += b.size        # 边界标记让合并退化为一次加法
        else:
            out.append(b)
    self.blocks = out
```

```python
def sweep(self):
    live = self.mark()
    for b in self.blocks:
        self.sweep_scanned += 1           # 清扫必须扫过整个堆
        if not b.free and b.payload not in live:
            b.free = True
            ...
    self.coalesce()                       # 清扫之后必须合并
```

## 七、性能与边界

- **最小可分配 chunk**：64 位 24 B（头 8 B + 两条 bin 链指针 16 B）、32 位 16 B。
  任何带 bookkeeping 且要求 8 字节对齐的分配器都逃不掉 16 B 这个下限。
- **mmap 阈值**（dlmalloc 默认 1 MB）需同时满足两个条件才启用：请求 > 阈值 **且** arena 里满足不了。
  独立映射的 chunk 释放后**永不与 arena 合并**（本 demo 用 `mmapped` 标记强制这一点）。
- **分割下限**：余数小于最小 chunk 时不分割，整块交付（本 demo `Heap(7).malloc(4)` 得到 usable 6 word，浪费 2 word）。
- **bin 编号**：< 512 B 时 `bin = ceil(size/8)`，共 63 档；≥ 512 B 时按「每翻倍 4 个 bin」近似对数间隔
  （dlmalloc 原文只说 *approximately logarithmically spaced*，未给具体档位表，此处为显式标注的一种读法）。

## 八、坑

1. **「best-fit 更省空间」不能当断言** —— 它依赖数据。本 demo 只断言**选中的块地址**这一确定量。
2. **相邻块必须真的地址相邻**才算可合并：本 demo 用 `addr + size == next.addr` 判定；
   列表里前后相邻不等于地址相邻（mmap 块就是反例）。
3. **延迟合并会让「总空闲量」与「最大可分配量」脱钩** —— 判断能否分配要看最大单块，不是看总和。
4. **清扫阶段扫描的块数会随分割历史变化**，断言时要用「清扫当时的块数」，不要用合并之后的块数。
5. Go 镜像里 `findFit` 需要同时处理「first 提前返回」与「dlmalloc 跳过 wilderness」两条分支，
   把 wilderness 也纳入 `best` 初值会导致策略退化成纯 best-fit。

## 九、参考资料（实际读过）

- Doug Lea, *A Memory Allocator*（dlmalloc 设计说明，边界标记 / 128 bin / best-first with coalescing /
  wilderness / mmap 阈值 1 MB / 延迟合并 / 最小 chunk 16·24 B 均出自此文）
  — <https://gee.cs.oswego.edu/dl/html/malloc.html>
- Doug Lea, `malloc-2.8.6.c`（dlmalloc 源码，chunk 布局与宏定义）
  — <https://gee.cs.oswego.edu/pub/misc/malloc-2.8.6.c>
- glibc `malloc/malloc.c`（dlmalloc 的后继，bins / tcache / consolidate 的落地）
  — <https://cdn.jsdelivr.net/gh/bminor/glibc@master/malloc/malloc.c>
- Memory Management Reference（mark-sweep / coalescing / fragmentation 的术语定义）
  — <https://www.memorymanagement.org/mmref/terminology.html>
- Linux man-pages `madvise(2)`（MADV_DONTNEED 对匿名私有映射 → 零页按需）
  — <https://man7.org/linux/man-pages/man2/madvise.2.html>
