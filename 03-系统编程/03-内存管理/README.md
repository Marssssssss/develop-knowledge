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

### 垃圾回收

- ✅ **三色标记（tri-color marking）** — [垃圾回收/gc-tri-color/](垃圾回收/gc-tri-color/)（C / Python / Go）
  - Dijkstra 1978 抽象：white/gray/black 三色 + worklist + tri-color 不变式
  - stop-the-world 版（无写屏障）演示 5 种图：可达链 / 循环 / 断连子图 / 钻石共享 / 孤立子树
  - 能回收 reference counting 不能回收的循环引用
  - 复杂度：mark O(E+V)、sweep O(heap size)；并发版需要 Dijkstra/Yuasa/SATB 写屏障

## 待研究

- [x] ~~简单 bump allocator 实现~~ (bump-allocator)
- [x] ~~Slab 分配器原理~~ (slab-allocator)
- [x] ~~GC 三色标记算法~~ (gc-tri-color)
- [ ] 复制式 GC（Cheney algorithm / 半空间）
- [ ] 分代 GC（young/old + remembered set）
- [ ] ptmalloc2 真实实现（arena + bins + thread cache）
- [ ] mmap / brk 虚拟内存基础
