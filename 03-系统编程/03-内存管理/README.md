# 内存管理

## 子领域

- **虚拟内存**：页表、TLB、缺页中断
- **堆分配器**：ptmalloc2 / jemalloc / tcmalloc / mimalloc
- **垃圾回收**：标记清除、复制、分代（G1/ZGC/Shenandoah）
- **内存池**：slab / arena

## 已完成 demo

### 分配器

- ✅ **bump (arena) allocator** — [分配器/bump-allocator/](分配器/bump-allocator/)（C / Python / Go）
  - `mmap` 单块 + 单调 `offset` + `(x + a - 1) & ~(a - 1)` 二进制向上对齐
  - O(1) 分配 / O(1) reset / 单块满返 NULL；多块版本需要链表（demo 内注明）
  - 4 个 demo 覆盖 basic / 32-byte 显式对齐（padding 演示）/ reset 复用 / OOM 边界
  - 适用：解析器一帧分配、帧分配器、etcd/bbolt 等

- ✅ **slab allocator** — [分配器/slab-allocator/](分配器/slab-allocator/)（C / Python / Go）
  - Bonwick 1994 简化版：`kmem_cache` + 三链表 `partial/full/free` + 单 slab `bitmap`
  - O(1) partial 头分配 / 第一个空位 `ctz` / full→partial 升级
  - 3 个 demo 覆盖 basic alloc / 跨 SLAB_OBJ_MAX 触发新 slab / slab 内对象地址连续
  - 真实 Linux SLAB 还含 slab coloring / per-CPU array / shrink_slab 回收

- ✅ **mmap / brk 虚拟内存基础** — [分配器/mmap-brk-虚拟内存/](分配器/mmap-brk-虚拟内存/)（C / Python / Go）
  - `brk` 程序 break 语义；三次"返回值口径"差异（syscall 返新 break / glibc 包装 0-1 / `sbrk` 返**旧** break）
  - `mmap` 的页对齐（巨页要巨页对齐）、`MAP_ANONYMOUS` 清零、`MAP_FIXED` 只丢弃**重叠部分**、`MAP_FAILED = (void*)-1`
  - `mallopt`：`M_MMAP_THRESHOLD` 默认 128 KiB、64 位上 `DEFAULT_MMAP_THRESHOLD_MAX = 32 MiB`、**动态 mmap 阈值**、trim 阈值 = 动态阈值 ×2、显式设置任一参数即**禁用动态调整**
  - 63 断言；无 C 工具链的部分走人工审查

- ✅ **ptmalloc2 真实实现** — [分配器/ptmalloc2/](分配器/ptmalloc2/)（Python / Go）
  - 16 字节 chunk 头（`prev_size` + `size|P|M|A`），`PREV_INUSE=0x01` / `IS_MMAPPED=0x02` / `NON_MAIN_ARENA=0x04`
  - `request2size` 原式（`MIN_CHUNK_SIZE = 32`、`MALLOC_ALIGNMENT = 16`）、`bin_index`（`NSMALLBINS=64`、`NBINS=128`、`MIN_LARGE_SIZE=1024`）
  - tcache **只做精确匹配**（`tidx2csize`/`csize2tidx`）；master 版 `TCACHE_MAX_BINS=76`/`FILL_COUNT=16`，2.35 版 `64`/`7`
  - malloc/free 的逐步骤查找顺序、arena 上限 = 8×CPU、free 不还给 OS
  - 83 断言

### 垃圾回收

- ✅ **三色标记（tri-color marking）** — [垃圾回收/gc-tri-color/](垃圾回收/gc-tri-color/)（C / Python / Go）
  - Dijkstra 1978 抽象：white/gray/black 三色 + worklist + tri-color 不变式
  - stop-the-world 版（无写屏障）演示 5 种图：可达链 / 循环 / 断连子图 / 钻石共享 / 孤立子树
  - 能回收 reference counting 不能回收的循环引用
  - 复杂度：mark O(E+V)、sweep O(heap size)；并发版需要 Dijkstra/Yuasa/SATB 写屏障

- ✅ **Cheney 半空间复制式 GC** — [垃圾回收/gc-cheney-copying/](垃圾回收/gc-cheney-copying/)（Python / Go）
  - from-space / to-space、`scan`/`alloc` 双指针即队列（**无递归栈**）、转发指针、BFS 层序布局
  - **引用更新与复制交错执行**；空间 2×、成本 ∝ 存活数
  - 并行版靠转发指针 + CAS 同步；保守式 GC 不能移动对象
  - 59 断言

- ✅ **标记-压缩式 GC** — [垃圾回收/gc-mark-compact/](垃圾回收/gc-mark-compact/)（Python / Go）
  - Lisp2 保序滑动：多遍顺序扫描 + `old -> new` **转发表**（`O(live)` 空间）
  - GHC `Compact.c` 的**线程化（threading）**：把"谁指向我"的链藏在对象自己的 info 槽里，解链时边走边算新地址，**额外空间 O(1)**；`tag ∈ {1,2}` 编在"指向槽的指针"上
  - 局部性对比：保序 vs Cheney 层序，按引用遍历的地址跳跃 20 vs 10（**恰好 2 倍**）
  - V8 的 `UpdatePointers` 是独立可观测状态（`are_map_pointers_encoded() == (state_ == UPDATE_POINTERS)`）
  - 52 断言

- ✅ **分代式 GC** — [垃圾回收/gc-generational/](垃圾回收/gc-generational/)（Python / Go）
  - eden + 双 survivor + 老年代；minor 复制/年龄+1/晋升，major 收整个堆
  - **写屏障 + 记忆集**是正确性前提：少了它会 reclaim 掉仍可达的新生代对象（demo2 给出悬空三元组）；晋升会"新造"老→新引用、死亡对象要从记忆集清掉
  - 成本实测：`nursery 12 字 + 老年代 2400 字`、2000 字存活集、4000 字 churn → 分代扫 **4008 字** vs 基线 **21708 字**（**5.4x**）
  - CPython 三代对照：`Python/gc.c` 的 `gc_select_generation` 上有条 **25% long_lived 启发式**（保证摊还线性）；默认阈值 **3.13 起从 700 变 2000**（实测 3.13.14 / 3.14.6 均为 `(2000, 10, 10)`）
  - 79 断言

## 待研究

- [x] ~~简单 bump allocator 实现~~ (bump-allocator)
- [x] ~~Slab 分配器原理~~ (slab-allocator)
- [x] ~~GC 三色标记算法~~ (gc-tri-color)
- [x] ~~复制式 GC（Cheney algorithm / 半空间）~~ (gc-cheney-copying)
- [x] ~~分代 GC（young/old + remembered set）~~ (gc-generational)
- [x] ~~ptmalloc2 真实实现（arena + bins + thread cache）~~ (ptmalloc2)
- [x] ~~mmap / brk 虚拟内存基础~~ (mmap-brk-虚拟内存)
- [ ] jemalloc / tcmalloc / mimalloc 的分级设计对比
- [ ] 标记-清除（mark-sweep）与空闲链表合并（coalescing）
- [ ] ZGC / Shenandoah 的并发压缩与着色指针
- [ ] 内存池之外的：per-CPU 分配与 NUMA 感知分配

## 参考资料

- man7：`brk(2)` / `mmap(2)` / `mallopt(3)` / `madvise(2)` — <https://man7.org/linux/man-pages/man2/mmap.2.html>
- glibc wiki *MallocInternals*（chunk 布局、bins、tcache）：<https://sourceware.org/glibc/wiki/MallocInternals>
- Memory Management Reference（mark-compact / compaction / copying 术语的权威定义）：<https://www.memorymanagement.org/mmref/terminology.html>
- GHC RTS `rts/sm/Compact.c`（线程化压缩）：<https://gitlab.haskell.org/ghc/ghc/-/blob/master/rts/sm/Compact.c>
- Oracle《Java SE 8 HotSpot VM GC Tuning Guide》第 3 章 *Generations*：<https://docs.oracle.com/javase/8/docs/technotes/guides/vm/gctuning/generations.html>
- CPython `Python/gc.c`（三代判定与 25% long_lived 启发式）：<https://github.com/python/cpython/blob/main/Python/gc.c>
