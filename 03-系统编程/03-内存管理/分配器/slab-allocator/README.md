# Slab Allocator — 最小对象缓存分配器

## 简介

Slab allocator 是 Sun Microsystems **Jeff Bonwick** 在 1994 年提出的内核内存分配算法,
目的是解决伙伴系统(buddy allocator)对小对象既慢又浪费的问题。
Linux 在 2.1.23 引入实现,FreeBSD 的 UMA、NetBSD 的 pool、Solaris 的 kmem 都是它的变体。

- 关键概念:**object cache**(同类型对象的高速缓存)、**slab**(1 页或多页的连续块,切成固定大小 slot)、**per-slab free bitmap**(位图标记每个 slot 是否已用)、**slab coloring**(slab 内偏移错开 CPU cache line)。
- 三大目标(原文,见参考资料):
  1. 分配小内存块,消除 buddy 系统造成的内部碎片
  2. 缓存常用对象,**保留已初始化状态**,避免反复 alloc/init/free
  3. **对齐到 L1/L2 cache 行**,提高硬件缓存利用率

## 原理详解

### 三级层级(cache → slab → object)

```text
+-----------------------------------------------------+
|              kmem_cache_chain (循环双链表)            |
+----+-----------------+-----------------+-------------+
   |                 |                 |
cache A           cache B           cache C
(struct file)     (struct inode)    (size-32 generic)
   |                 |                 |
   v                 v                 v
+---------+    +---------+       +---------+
|  slab   |    |  slab   |       |  slab   |   <- 每个 slab = 1+ page
| page    |    | page    |       | page    |
+---------+    +---------+       +---------+
|o|o|o|o| |   |o|o|o|o| |       |o|o|o|o| |   <- object (固定 size)
|o|o|o|o| |   |o|o|o|o| |       |o|o|o|o| |
|o|o|o|o| |   |o|o|o|o| |       |o|o|o|o| |
+---------+    +---------+       +---------+
  color 0        color 1          color 2
```

cache 有三条 slab 链表:
- `slabs_partial`:有空闲 slot,优先从这里分配(局部性好)
- `slabs_full`:完全分配满,新增 cache 会来这找 → 全满触发新建
- `slabs_free`:空 slab(尚未分配过任何对象)

分配路径:**partial → free → 新建**(放 partial),这样大多数热路径都命中 partial,cache line 复用最好。

### 分配步骤

1. `kmem_cache_select_slab(c)`:
   - 若 `c->slabs_partial` 非空 → 取链表头(局部性最好)
   - 否则若 `c->slabs_free` 非空 → 从 free 移动到 partial
   - 否则(全 full)→ `slab_create()` 新建一个 slab 进 partial
2. 在 slab 内用 **bitmap** 找空闲 slot:典型做法是 `~bitmap & MASK` 拿到空闲位,然后 `ctz`(count trailing zeros)找到最低位的 1,得到 slot 索引。
3. `bitmap |= (1 << idx)`,`used++`,返回 `slab->mem + idx * obj_size`。
4. 若 slab 满:从 partial 移到 full。

### 释放步骤

1. 根据对象地址反算 `slab` 与 `idx`:
   - O(1) 方法:每个 slab 在对象前面放 `slab_prologue`(元数据头),对象地址偏移前 sizeof(prologue) 即可拿到 slab 指针 —— **生产 SLUB 采用此法**(本 demo 简化版,遍历链表)。
2. `bitmap &= ~(1 << idx)`,`used--`。
3. 若 slab 之前在 `slabs_full`,刚释放后移回 `slabs_partial`。
4. 真正的回收(把空 slab 整体还给伙伴系统)由 `reap_timer`(Linux 2.4) / `shrink_slab`(Linux 2.6+)异步触发,本 demo 不实现。

### 核心 API(简化对应到本 demo)

```c
kmem_cache_t *kmem_cache_create(size_t obj_size);
void          kmem_cache_destroy(kmem_cache_t *c);
void         *kmem_cache_alloc(kmem_cache_t *c);    /* 返回 slot 指针 */
void          kmem_cache_free(kmem_cache_t *c, void *obj);
```

每个参数语义:
- `obj_size`:由调用方指定(应当 2 的幂或对齐到平台 L1 cacheline 大小)
- 返回的对象地址**已经在内部 cache 对齐**(`__alignof__(obj)` 必满足)
- 错误处理:创建失败返回 NULL(`calloc` 失败 / OOM);free 越界对象会触发断言/打印

### 底层发生了什么(内核视角)

1. slab 用 `__get_free_pages(GFP_KERNEL, order)` 从伙伴系统拿 1 页或多页连续内存
2. slab 内对象 slot 起始地址按 `colour_off` 偏移错开 — 这就是 **slab coloring**:
   - 同一 cache 中不同 slab 的对象起始偏移不同,确保落在不同 CPU cache line
   - 偏移量 = `(colour * BYTES_PER_WORD) % cacheline`,消除 cache 串扰
3. kmalloc 的 size-N 缓存(32B 到 131072B)实际上就是 cache 矩阵,`kmalloc(size, GFP_KERNEL)` 选合适 cache 然后调用 `kmem_cache_alloc`。
4. 高频路径加 **per-CPU array** (`array_cache_t`):CPU 本地缓存 25-50 个对象,无锁分配;耗尽再批量从共享 cache 取 — 这是 SLAB 在多核下不退化的关键。

## 对比 / 选型

| 维度 | SLAB | SLUB(SLK 4.17+ 默认) | SLOB | bump allocator | mark-sweep GC |
| --- | --- | --- | --- | --- | --- |
| 历史 | Linux 2.1.23+ | Linux 2.6.23+ | 嵌入式 | 教学/demo | JVM/Go runtime |
| 数据结构 | 三链表 + per-cpu | 单 freelist(嵌入 slot 内) | first-fit | 单一 offset | 三色 / 标记位 |
| 元数据大小 | 大(per-slab 控制头) | 小 | 极小 | 极小(只要 offset) | per-object 2bits |
| 适合对象尺寸 | 固定尺寸高频 | 任何尺寸 | 极小内存(<64KB) | 任意(同帧) | 长生命周期 |
| 时间复杂度 | O(1) 局部分配,O(n) 跨 slab 查找 | O(1) | O(n) 扫描 | O(1) | O(V+E) mark |
| 缓存命中 | 高(per-cpu array) | 高 | 低(无 per-cpu) | 极高(同页) | 一般 |
| 实现复杂度 | 高 | 中 | 低 | 极低 | 很高 |

真实生产:
- Linux 默认 → SLUB(2017 起移除 SLAB 选项)
- FreeBSD → UMA(jemalloc 启发)
- Memcached → slab allocators for keys/values
- jemalloc → 多 arena + 多个 bin slabs

## 环境准备

- 操作系统:Linux / macOS / WSL(Windows 需 POSIX 环境)
- C:`gcc` / `clang`(只需 `<stdio.h> <stdlib.h> <string.h>`,无须 `<linux/slab.h>`)
- Python:3.10+(用到 `ctypes.addressof` 取 memoryview 底层地址)
- Go:1.21+

## 运行方式

```bash
# C
gcc -O2 -Wall -Wextra slab_allocator.c -o slab_demo
./slab_demo

# Python
python3 slab_allocator.py

# Go
go run slab_allocator.go
```

## 关键代码片段

### C:`__builtin_ctz(~bitmap)` 找第一个空闲 slot

```c
static int bitmap_find_free(uint32_t *bm, int n) {
    /* ~bm 把空闲位变 1,ctz 找出最低位的 1(第一个空 slot)        */
    return __builtin_ctz(~bm);
}

/* 分配主流程 */
void *kmem_cache_alloc(kmem_cache_t *c) {
    slab_t *s = kmem_cache_select_slab(c);
    int idx = bitmap_find_free(s->bitmap, s->obj_count);

    bitmap_set_used(s->bitmap, idx);   /* bm |= 1 << idx          */
    s->used++;
    if (s->used == s->obj_count) {     /* slab 满 → full 链         */
        c->slabs_partial = s->next;
        s->next = c->slabs_full;
        c->slabs_full = s;
    }
    return s->mem + (size_t)idx * c->obj_size;
}
```

### Python:`(free_mask & -free_mask).bit_length() - 1` 替代 ctz

```python
free_mask = ~s.bitmap & ((1 << s.obj_count) - 1)
idx = (free_mask & -free_mask).bit_length() - 1     # 等价 ctz32
s.bitmap |= (1 << idx)
```

### Go:`uintptr(unsafe.Pointer(&obj[0]))` 反算 slab + idx

```go
objAddr := uintptr(unsafe.Pointer(&obj[0]))
// 遍历 slab 链表,基地址 ≤ objAddr < 基地址 + obj_count*obj_size 即命中
baseAddr := uintptr(unsafe.Pointer(&cur.mem[0]))
endAddr  := baseAddr + uintptr(cur.objCount*c.objSize)
if baseAddr <= objAddr && objAddr < endAddr {
    idx := int((objAddr - baseAddr) / uintptr(c.objSize))
    cur.bitmap &^= (1 << idx)        // 释放该位
}
```

## 性能与边界

- **分配复杂度**:`O(1)`(在 partial 链表头 → 1 次 `ctz`)
- **释放复杂度**:`O(n_slab)`(遍历 partial+full 找 obj 所属 slab;生产 SLUB 用 prologue 优化为 `O(1)`)
- **空间开销**:每个 slab 一个 `uint32 bitmap`(若 `obj_count > 32` 则需多个字);
  对齐到 cacheline 会浪费少量尾部空间
- **多核**:本 demo 是**单线程**;生产 SLAB 加 per-CPU array 避免共享锁争用
- **回收时机**:本 demo 不主动回收空 slab;Linux 由 `shrink_slab` 周期回收

## 注意事项与常见坑

1. **对象尺寸固定**:slab cache 只能管理一种尺寸对象;不同尺寸必须各自建 cache,这正是 `kmalloc` 用 size-N 矩阵(many caches)的原因。
2. **大小应向上对齐**到 alignment(平台 8/16/32 字节,或 L1 cacheline 大小 64 字节);否则频繁跨 cache line。
3. **per-CPU array 的批量补/退**:CPU 本地缓存耗尽时批量从共享 cache 取 25-50 个对象(溢出阈值),回到本地;大量跨核分配会让 per-CPU 频繁 rebalance,反而慢。
4. **slab coloring 必不可少**:不染色会导致同一 cache 的不同 slab 对象落到同一 cache line,被互相挤掉。Bonwick 论文里专门讨论这一点。
5. **复用率与寿命**:slab 假设同类对象寿命相近 — 短寿命对象可以从一个 slab 清掉、长寿命进另一个 slab,大幅减少碎片。Linux mm 引入了 **slab batching 与 CGROUP memory accounting** 强化控制。
6. **释放指针必须属于同一 cache**:释放不属于本 cache 的指针会报错或释放给错误的 slot(本 demo 简单打印 `not in cache`)。
7. **回收(reap)的时机**:
   - 普通路径只把 full → partial,**不**主动释放空 slab
   - 系统内存压力大时由 `kswapd` / `shrink_slab` 把空闲 slab 还给伙伴
   - 错误做法:在每次 free 时检查并 munmap,会让单次 free 开销爆增、cache 局部性也毁了
8. **debug 钩子**:生产 SLAB 提供了 `slab_debug`、`poisoning`、`red zone` 检测越界写;教学 demo 不包含。
9. **碎片内化**:虽然 Bonwick 提出"slab 同类对象寿命相近"有效减少外部碎片,但长时间运行下仍可能有碎片。**SLUB** 的简化 freelist 嵌入对象空间额外降低元数据开销。
10. **64 位结构体对齐**:toy struct 内手动 `pad[27]` 凑齐 32 字节,这是模拟真实内核对象(如 `struct file`)多个 cacheline 成员;否则 demo 的 sizeof 不会是 32。

## 参考资料(实际阅读过的权威来源)

- [Chapter 8: Slab Allocator — Understanding the Linux Kernel (kernel.org)](https://kernel.org/doc/gorman/html/understand/understand011.html) — slab 三大目标原文 + cache/slab/object 层级图解 + slab coloring 机制 + kmalloc 的 size-N 缓存实现。
- [Bonwick, "The Slab Allocator" (USENIX Summer 1994)](https://www.usenix.org/node/104358) — 原始论文:USENIX 论文综述 + abstract(SunOS 5.4 内核分配器、缓存构造好的对象、对象着色减少 cache 抖动)。
- [illumos-gate kmem.c — NJU 镜像](https://git.nju.edu.cn/nju/illumos-gate/-/blob/master/usr/src/uts/common/os/kmem.c) — 现代 Solaris/illumos 实现头部注释:Bonwick94 + Bonwick&Adams 2001 + consolidator 设计动机。
- [Papers We Love: Slab Allocator](https://paperswelove.org/papers/the-slab-allocator-an-object-caching-kernel-memory-91047b44) — 论文摘要 + 影响力追溯:Perl 5、Memcached、Hurd、L4、Linux mm/slab.c 的引用脉络。
- [HandWiki: Slab allocation](https://handwiki.org/wiki/index.php?oldid=4347054&title=Slab_allocation) — SLUB freelist 嵌入空闲对象的设计差别 + 多个采用 slab 的系统列表(AmigaOS / FreeBSD / HP-UX / Memcached / Solaris 等)。
