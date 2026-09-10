# 内存管理

## 子领域

- **虚拟内存**：页表、TLB、缺页中断
- **堆分配器**：ptmalloc2 / jemalloc / tcmalloc / mimalloc
- **垃圾回收**：标记清除、复制、分代（G1/ZGC/Shenandoah）
- **内存池**：slab / arena

## 待研究

- [ ] 简单 bump allocator 实现
- [ ] Slab 分配器原理
- [ ] GC 三色标记算法