# Memcached Slab 分配器

## 一、简介

Memcached 不用 `malloc/free` 逐条分配，而是自己实现了一套 **slab 分配器**：把内存切成固定 1MB 的页，每页再按**同一个尺寸**切成 chunk，不同尺寸的 chunk 归到不同的 **slab class**。这样既避免了通用分配器的开销，也从根本上消除了**外部碎片**——代价是**内部碎片**，以及一个更隐蔽的问题：**内存会卡死在某个 class 里**。

本 demo 复现 `slabs_init()` 的建表算法，量化碎片率，并演示「明明还有内存却在淘汰」的成因。

## 二、原理详解

### 2.1 三个层次的尺寸

`slabs.c` 头部注释把结构说得很清楚：

> Slabs memory allocation, based on powers-of-N. Slabs are up to 1MB in size and are divided into chunks. The chunk sizes start off at the size of the "item" structure plus space for a small key and value. They increase by a multiplier factor from there, up to half the maximum slab size.

| 层次 | 默认 | 来源 |
| --- | --- | --- |
| slab 页（page） | **1 MB** | `settings.slab_page_size` |
| 最大 chunk | **512 KB** = page/2 | `settings.slab_chunk_size_max = slab_page_size / 2` |
| 起始 chunk | **96 字节** = `sizeof(item)` + 48 | `settings.chunk_size = 48` |
| 增长倍率 | **1.25** | `settings.factor` |
| 单 item 上限 | **1 MB** | `settings.item_size_max`（*The famous 1MB upper limit*） |

注意 **1MB 的 item 上限 ≠ 512KB 的 chunk 上限**：超过 512KB 的 item 必须拆成多个 512KB 的 chunk（`ITEM_CHUNKED`），这也是 `slabs_clsid()` 对超过 512KB 的尺寸返回 0（放不下）的原因。

### 2.2 `sizeof(item)` = 48

`memcached.h` 的 `_stritem` 在 LP64 下：

| 字段 | 字节 |
| --- | --- |
| `next` / `prev` / `h_next`（三个指针） | 24 |
| `time` / `exptime` / `nbytes`（rel_time_t ×2 + int） | 12 |
| `refcount` / `it_flags` / `slabs_clsid` / `nkey` | 6 |
| 合计 | 42 |
| `data[]`（`union { uint64_t cas; char end; }`，8 字节对齐）补齐 | **48** |

柔性数组 `data[]` 不计入 `sizeof`，所以 `sizeof(item) = 48`。起始 chunk = `48 + settings.chunk_size(48)` = **96 字节**。

### 2.3 建表算法（slabs_init）

```c
int i = POWER_SMALLEST - 1;
unsigned int size = sizeof(item) + settings.chunk_size;   /* 96 */
while (++i < MAX_NUMBER_OF_SLAB_CLASSES-1) {
    if (size >= settings.slab_chunk_size_max / factor) break;
    if (size % CHUNK_ALIGN_BYTES)                          /* 8 字节对齐 */
        size += CHUNK_ALIGN_BYTES - (size % CHUNK_ALIGN_BYTES);
    slabclass[i].size = size;
    slabclass[i].perslab = settings.slab_page_size / slabclass[i].size;
    size *= factor;
}
power_largest = i;
slabclass[power_largest].size = settings.slab_chunk_size_max;   /* 特殊处理 */
slabclass[power_largest].perslab = settings.slab_page_size / settings.slab_chunk_size_max;
```

三个关键点：

1. **退出条件是 `size >= chunk_size_max / factor`，不是 `MAX_NUMBER_OF_SLAB_CLASSES`（64）**。默认参数下只建出 **39 个 class**，上限常量根本没用满。
2. **`power_largest` 单独处理**，直接设成 `chunk_size_max`（512KB），而不是继续按倍率乘。
3. **`perslab` 是向下取整**，所以每页尾部都会剩 `page - perslab × size < size` 的零头（尾部碎片）。

默认参数下前几类（本 demo 实测）：

| class | chunk 尺寸 | perslab（每页几个） |
| --- | --- | --- |
| 1 | 96 | 10922 |
| 2 | 120 | 8738 |
| 3 | 152 | 6898 |
| 4 | 192 | 5461 |
| … | … | … |
| 39（power_largest） | 524288 | 2 |

注意 class 2 → 3 是 `120 × 1.25 = 150`，但被 8 字节对齐成了 **152** —— 对齐会轻微放大实际倍率。

### 2.4 内部碎片：factor 是唯一的旋钮

`slabs_clsid()` 找的是**第一个 `size >= ntotal` 的 class**，多出来的就是内部碎片：

- 100 字节的 item → class 2（120），浪费 **20 字节（16.7%）**
- 500 字节的 item → class 9（600），浪费 **100 字节（16.7%）**
- **97 字节** → class 1（96）装不下，只能进 class 2（120），浪费 **23 字节**

最坏情况是「刚好比某个 class 大 1 字节」，碎片率趋近 `factor - 1 = 25%`；尺寸在各类间均匀分布时平均碎片率约 `(factor-1)/(2·factor) = 10%`（本 demo 实测均在此附近）。

所以 **factor 就是内存效率与 class 数量的取舍**：factor 越接近 1，碎片越少，但 class 数暴涨、管理开销和「卡死」风险上升。

### 2.5 内存卡死在某个 class（calc'ing）

页一旦分配给某个 class 就**不会自动还回来**。于是：

1. 业务先写满 100 字节的 item → 64MB 全部分给 class 2；
2. 业务改用 500 字节的 item → 需要 class 9，但 class 9 一页都没有；
3. memcached **不会**把 class 2 的空闲页挪给 class 9，只能在 class 9 内部淘汰 → **命中率断崖**；
4. 而此时 class 2 里可能还有大量空闲 chunk —— 这就是「内存没满却在淘汰」。

本 demo 实测：64 页全给 class 2 后，连续写 1000 个大 item，**1000 次全部触发淘汰，一个都没存下**。

出路是 **slab reassignment / automove**（把页从一个 class 挪给另一个），但它有一个硬约束：**只有当整页的 chunk 全部空闲时才能挪**。本 demo 实测：腾空 class 2 最后一页后可以挪，且挪完之后大 item 立刻能存进去；而「最后一页仍有在用 chunk」时 `reassign` 直接失败。

## 三、对比

| 维度 | 通用 `malloc/free` | slab 分配器 |
| --- | --- | --- |
| 外部碎片 | 有（长期运行后严重） | **没有**（页内等分） |
| 内部碎片 | 小（按请求尺寸） | 有（平均 ~10%，最坏 ~25%） |
| 分配开销 | 高（锁 + 空闲链表查找） | 低（从本 class 的空闲链表 pop） |
| 跨 class 弹性 | 天然有 | **没有**，需 slab reassignment |
| 典型问题 | 碎片化、锁竞争 | 内存卡在某个 class |

## 四、环境

- Python 3.13（纯标准库）
- C（C11，`gcc -std=c11 -O2 slab_alloc.c -o sa && ./sa`）
- Go 1.22+（仅 `fmt`）

## 五、运行方式

```bash
cd 07-数据存储/03-缓存/MemcachedSlab分配
python slab_alloc_selftest.py     # 44 条断言
gcc -std=c11 -O2 slab_alloc.c -o sa && ./sa
go run slab_alloc.go
```

## 六、关键代码

建表循环（Python 版，与 `slabs_init` 逐行对应）：

```python
i = POWER_SMALLEST - 1
size = sizeof_item + chunk_size
while True:
    i += 1
    if i >= max_classes - 1: break
    if size >= chunk_size_max / factor: break      # 注意除的是 factor
    size = align_up(size)                          # 8 字节对齐
    out.append((i, size, page_size // size))
    size = int(size * factor)
out.append((i, chunk_size_max, page_size // chunk_size_max))   # power_largest
```

`slabs_clsid()` 的语义就是「第一个装得下的 class」——多出来的部分全是浪费：

```python
def class_for(ntotal, classes):
    for cid, size, _ in classes:
        if ntotal <= size:
            return cid
    return 0        # 0 = 放不下（超过 chunk_size_max，需 ITEM_CHUNKED）
```

## 七、性能边界

- 分配/释放是 O(1)（本 class 空闲链表 pop/push），且**不会**产生外部碎片
- 内部碎片与 factor 强相关：factor 从 1.25 降到 1.05，碎片率降到 ~2.4%，但 class 数从 39 涨到约 **190**，超过 `MAX_NUMBER_OF_SLAB_CLASSES`(64) 后会被截断——**factor 不能一味调小**
- 单 item 1MB 上限是硬编码默认值，超大 value 必须业务侧拆分
- 页分配是**单调不可逆**的（除非手动 reassignment），工作负载尺寸分布剧烈变化时会长期处于次优状态
- slab reassignment 要求整页空闲，实际生产中能挪动的页往往很少

## 八、注意事项与常见坑

1. **`item_size_max`(1MB) ≠ `slab_chunk_size_max`(512KB)**：超过 512KB 的 item 走 chunked 路径，性能特征完全不同。
2. **退出条件是 `size >= chunk_size_max / factor`**，class 数（默认 39）远小于 `MAX_NUMBER_OF_SLAB_CLASSES`(64)。
3. **`power_largest` 被强制设为 512KB**，不遵循倍率增长。
4. **8 字节对齐会放大实际倍率**（120 × 1.25 = 150 → 152）。
5. **「刚好多 1 字节」就跳一整级**，业务上把 value 控制在 class 边界内是实打实的内存优化。
6. **页一旦分配不会自动归还**，工作负载尺寸切换会导致「内存没满却在淘汰」。
7. **slab reassignment 只在整页空闲时可用**，不要指望它兜底。
8. **`perslab` 是向下取整**，每页尾部都有零头，class 越大（chunk 越接近 512KB）尾部浪费越显著（最后一类只剩 2 个 chunk）。
9. **本 demo 的页模型是简化版**：真实的 LRU 是 per-slab-class 的，且 chunk 回收进的是本 class 的空闲链表而非全局。

## 九、参考资料

- `memcached/slabs.c` — 头部注释（powers-of-N、1MB slab、chunk 起始与增长规则）、`slabs_init()` 建表循环、`slabclass_t` 结构、`do_slabs_newslab()` 与 `mem_limit` 判定（本轮抓取自 `raw.githubusercontent.com/memcached/memcached/master/slabs.c`）
- `memcached/memcached.c` — settings 默认值：`maxbytes = 64MB`、`factor = 1.25`、`item_size_max = 1024*1024`（*The famous 1MB upper limit*）、`slab_page_size = 1024*1024`、`slab_chunk_size_max = slab_page_size/2`、`chunk_size = 48`
- `memcached/memcached.h` — `CHUNK_ALIGN_BYTES 8`、`MAX_NUMBER_OF_SLAB_CLASSES (63+1)`、`POWER_SMALLEST 1`、`POWER_LARGEST 256`、`_stritem` 结构体
- `memcached/items.c` — `item_make_header()`（`sizeof(item) + nkey + nsuffix + nbytes`）、`slabs_clsid()` 调用点
