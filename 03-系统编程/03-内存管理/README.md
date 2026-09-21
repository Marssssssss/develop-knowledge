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

- ✅ **分级分配器对比（jemalloc / tcmalloc / mimalloc）** — [分配器/分级分配器对比/](分配器/分级分配器对比/)（Python / Go）
  - jemalloc：quantum 16 B、**每个翻倍 4 档**（间隔 = 2ᵏ/4）、内部碎片最坏 `(S-1)/5S → 20%`；
    small/large 分界 = 4×页 = 16 KiB；`narenas = 4×CPU`；dirty decay 默认 10 s、muzzy 默认关闭
  - tcmalloc：60~80 档；**静态容量 = 相邻两档数组起点之差 / 指针大小**，运行期容量被它夹住；
    档位耗尽可从同 CPU 的其它档「偷」容量；总缓存随 CPU 数线性增长
  - mimalloc（`types.h` 实读）：small/medium/large 页 = 64 KiB / 512 KiB / 4 MiB，
    对象上限 = (页 − 4 KiB)/6 → 10 KiB / 86698 B；`MI_BIN_HUGE = 73`
  - 65 断言；记录了官方 Table 1 与递推规则在 8~16 KiB 区间的**不一致**

- ✅ **per-CPU 分配与 NUMA 感知分配** — [分配器/percpu-numa/](分配器/percpu-numa/)（Python / Go）
  - `this_cpu_*` 自带抢占保护 vs `__this_cpu_*` 无保护：RMW 之间被迁移会把更新写到**错误 CPU 的副本**（总数 11 ≠ 6）
  - 共享计数器 12 次写 → 11 次 cacheline 失效；per-CPU 计数器 0 次
  - jemalloc `percpu_arena`：percpu = arena 随 CPU 变；phycpu = 同核两个超线程共享一个 arena
  - NUMA 七种策略：`MPOL_BIND` 取**最近**而非最小节点号（2.6.26 语义变更）、`MPOL_MF_MOVE` 只搬独占页、
    `MPOL_MF_MOVE_ALL` 需 `CAP_SYS_NICE`、四种 EINVAL 组合
  - 49 断言

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

- ✅ **标记-清除与空闲链表合并** — [垃圾回收/mark-sweep-free-list/](垃圾回收/mark-sweep-free-list/)（Python / Go）
  - dlmalloc 两大不变式：**边界标记**（footer 让向后合并 O(1)，新版本在用 chunk 省略 trailer）+ **分箱**
    （128 个近似对数 bin，< 512 B 每箱一种尺寸、间隔 8 B）
  - **best-first with coalescing**；bin 内按尺寸升序、同尺寸最旧优先；wilderness 当「最大」只在无其它候选时用
  - 延迟合并会造成**假性 OOM**（15 word 空闲但 9 word 请求失败）；mmap 块永不并入 arena
  - 最小 chunk：32 位 16 B / 64 位 24 B；环形垃圾 mark-sweep 收 2 个、引用计数收 0 个
  - 60 断言

- ✅ **ZGC 着色指针与并发压缩** — [垃圾回收/zgc-colored-pointers/](垃圾回收/zgc-colored-pointers/)（Python / Go）
  - `zAddress.hpp` 实读的 16 位元数据布局 `RRRRMMmmFFrr0000`：Reserved(0,4) / Remembered(4,2) /
    Marked(6,6) / Remapped(12,4)；掩码 `Load 0xF000 ⊆ Mark 0xFFC0 ⊆ Store 0xFFF0`
  - `ZPointerLoadShiftTable`：Null→24、Remapped00/01/10/11→13/14/15/16（重叠零位 3/2/1/0）
  - RemappedOld/Young 掩码交替 ⇒ remap 位按 `00→01→11→10→00` 四步循环
  - load barrier **自愈**：颜色过期 → 慢路径查转发表 → 把修正后的指针写回字段
  - 对照 Shenandoah 的 Brooks pointer：LRB 每次 load 都要多读一次对象头（1:1）
  - 63 断言

### 虚拟内存

- ✅ **多级页表、TLB 与缺页中断** — [虚拟内存/多级页表与TLB/](虚拟内存/多级页表与TLB/)（Python / Go）
  - 4 KiB 页 + 每级 9 位；4 级 48 位 / 5 级 57 位；遍历代价 = 级数 + 1
  - 规范地址 = 高位符号扩展：`0x0000800000000000` 与 `0x0100000000000000` 分别是 4/5 级的非规范空洞
  - 内核布局原文数值（直接映射 / vmalloc / vmemmap / 内核文本 512 MB 映射到物理 0）
  - 5 级把上限抬到 **128 PiB 虚拟 / 4 PiB 物理**（×512 / ×64），但默认仍只用 47 位
  - 缺页分类：匿名首次访问与 **COW 都是 minor**；VMA 只读的写、PROT_NONE 访问才是 SIGSEGV
  - userfaultfd 四种注册模式，`WP` 与 `RWP` 互斥、`MISSING|WP` 合法
  - 62 断言

## 待研究

- [x] ~~简单 bump allocator 实现~~ (bump-allocator)
- [x] ~~Slab 分配器原理~~ (slab-allocator)
- [x] ~~GC 三色标记算法~~ (gc-tri-color)
- [x] ~~复制式 GC（Cheney algorithm / 半空间）~~ (gc-cheney-copying)
- [x] ~~分代 GC（young/old + remembered set）~~ (gc-generational)
- [x] ~~ptmalloc2 真实实现（arena + bins + thread cache）~~ (ptmalloc2)
- [x] ~~mmap / brk 虚拟内存基础~~ (mmap-brk-虚拟内存)
- [x] ~~jemalloc / tcmalloc / mimalloc 的分级设计对比~~ (分配器/分级分配器对比)
- [x] ~~标记-清除（mark-sweep）与空闲链表合并（coalescing）~~ (垃圾回收/mark-sweep-free-list)
- [x] ~~ZGC / Shenandoah 的并发压缩与着色指针~~ (垃圾回收/zgc-colored-pointers)
- [x] per-CPU 分配与 NUMA 感知分配 (分配器/percpu-numa)
- [x] 虚拟内存：多级页表 / TLB / 缺页中断 (虚拟内存/多级页表与TLB)
- [ ] 透明巨页 THP（khugepaged / MADV_COLLAPSE）与巨页池
- [ ] 内存 cgroup（v2 memory.max / memory.high / psi）与 OOM killer 选择策略
- [ ] 用户态按需分页的延伸：userfaultfd 实现快照与实时迁移

## 参考资料

- man7：`brk(2)` / `mmap(2)` / `mallopt(3)` / `madvise(2)` — <https://man7.org/linux/man-pages/man2/mmap.2.html>
- glibc wiki *MallocInternals*（chunk 布局、bins、tcache）：<https://sourceware.org/glibc/wiki/MallocInternals>
- Memory Management Reference（mark-compact / compaction / copying 术语的权威定义）：<https://www.memorymanagement.org/mmref/terminology.html>
- GHC RTS `rts/sm/Compact.c`（线程化压缩）：<https://gitlab.haskell.org/ghc/ghc/-/blob/master/rts/sm/Compact.c>
- Oracle《Java SE 8 HotSpot VM GC Tuning Guide》第 3 章 *Generations*：<https://docs.oracle.com/javase/8/docs/technotes/guides/vm/gctuning/generations.html>
- CPython `Python/gc.c`（三代判定与 25% long_lived 启发式）：<https://github.com/python/cpython/blob/main/Python/gc.c>
- Doug Lea《A Memory Allocator》（dlmalloc 边界标记 / 128 bin / best-first + coalescing / wilderness / 最小 chunk）
  ：<https://gee.cs.oswego.edu/dl/html/malloc.html>
- jemalloc(3) man page（Table 1 档位表、narenas / percpu_arena / dirty·muzzy decay）：<https://jemalloc.net/jemalloc.3.html>
- TCMalloc Design Doc（per-CPU 静态容量、容量窃取、MaxPerCpuCacheSize）：<https://google.github.io/tcmalloc/design.html>
- mimalloc `include/mimalloc/types.h`（三级页尺寸、对象上限、`MI_BIN_HUGE=73`）
  ：<https://raw.githubusercontent.com/microsoft/mimalloc/dev3/include/mimalloc/types.h>
- OpenJDK `src/hotspot/share/gc/z/zAddress.hpp`（zpointer 16 位元数据布局与 load shift table）
  ：<https://raw.githubusercontent.com/openjdk/jdk/master/src/hotspot/share/gc/z/zAddress.hpp>
- JEP 333（ZGC 目标与并发压缩）/ JEP 439（分代 ZGC）：<https://openjdk.org/jeps/333> · <https://openjdk.org/jeps/439>
- Linux man-pages `mbind(2)` / `numa(7)`（七种 NUMA 策略与 flags）：<https://man7.org/linux/man-pages/man2/mbind.2.html>
- Linux `core-api/this_cpu_ops`（`this_cpu_*` 与 `__this_cpu_*` 的抢占保护差异）
  ：<https://www.kernel.org/doc/html/latest/core-api/this_cpu_ops.html>
- Linux `arch/x86/x86_64/mm.rst` / `5level-paging.rst`（虚拟内存映射表与 5 级页表容量）
  ：<https://www.kernel.org/doc/html/latest/arch/x86/x86_64/mm.html>
- Linux man-pages `userfaultfd(2)`（四种注册模式与互斥规则）：<https://man7.org/linux/man-pages/man2/userfaultfd.2.html>
