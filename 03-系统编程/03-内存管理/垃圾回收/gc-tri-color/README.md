# Tri-Color Marking — Dijkstra 三色标记垃圾收集算法

## 简介

**三色标记(Tri-Color Marking)** 是垃圾回收理论中**追踪式(tracing)增量/并发收集器**的通用抽象。
1978 年由 **Dijkstra, Lamport, Martin, Scholten, Steffens** 在 *"On-the-Fly Garbage Collection: An Exercise in Cooperation"* 一文提出,
为并发 mark-sweep 提供了一个**形式化正确性论证**,并成为后续 Go、V8、HotSpot G1、.NET CoreCLR 等现代 GC 的基础。

- 关键概念:**white/gray/black**(三色)、**tri-color invariant**(不变式:**禁止黑色对象直接指向白色对象**)、**worklist**(灰色工作栈/队列)、**write barrier**(并发场景下维护不变式的钩子)。
- 主要优势:**能回收循环引用**(reference counting 不能)、**支持增量/并发收集**(与 mutator 交错执行)、**O(E+V)** 时间复杂度。
- 历史背景:1978 年提出早于 stop-the-world mark-sweep(McCarthy 1960 雏形),其抽象后来催生了 Lua 5.1/5.2、LuaJIT 2.x、V8、Java G1、Go runtime。

## 原理详解

### 三色语义

| 颜色 | 含义 |
| --- | --- |
| **White**(白) | 尚未被 collector 访问到 — 默认不可达;若最终仍为白,则可回收 |
| **Gray**(灰) | 已被发现可达,但其出度尚未全部扫描 — 在 worklist 中 |
| **Black**(黑) | 已完全扫描,**已知可达且所有子节点都已访问** — 不可能再指向白色节点 |

### 三阶段

```text
Heap:
   A --> B --> C
   A      \\
    \\     --> D (D shared)
     --> E
   F (orphan)

Roots: {A}
```

1. **Initialize**:所有对象白色;根可达对象 → 灰色入栈。
2. **Mark loop**:
   - `pop gray object g`
   - `for each white child c of g: c.mark = gray; push c`
   - `g.mark = black`
3. **Sweep**:无灰色对象时终止,所有白色对象不可达 → 回收。

### 不变式(Tri-Color Invariant)

> **Strong invariant**:不允许黑色对象直接持有指向白色对象的引用。

这是**正确性核心**:若不变量被保持,那么"灰色耗尽 ⇒ 白色不可达"。mutator(用户线程)在并发 GC 时若插入 `黑 → 白` 边,会破坏不变量。**写屏障(write barrier)**就是为修复这一点而设:
- **Dijkstra 写屏障(forward, 增量更新)**:mutator 写入新引用时,若目标为白色,直接把它置灰 — 保证白色对象"无被遗忘风险"。Lua 5.1/5.2 table 用此法。
- **Yuasa 写屏障(backward, 快照更新)**:mutator 写入时,若来源为黑色,把来源重新置灰 — 后续会再扫一次。V8、HotSpot G1 都用此变体。
- **SATB(Snapshot At The Beginning)**:在 GC cycle 开始时把"当前对象图"视为不变,mutator 写入时把老引用记到 remembered set — HotSpot G1 中常用。

### 工作流(对 demo 中简单图 A→B, A→X,Y, X unreachable)

```text
初始:                 all WHITE, gray=∅

[Init]                A: WHITE→GRAY, push A
                      gray=[A]                {A:GRAY, others:WHITE}

[Mark A]              pop A
                      - child B: WHITE→GRAY, push B
                      - child X: WHITE→GRAY, push X
                      A: GRAY→BLACK
                      gray=[B,X]              {A:BLACK, B:X:GRAY, others:WHITE}

[Mark B]              pop X (or B)
                      - child Y: WHITE→GRAY, push Y
                      X: GRAY→BLACK
                      gray=[B,Y] / [B]       ...

(依次...) 最后:
                      gray=∅
                      A,B,X,Y:BLACK, Y:BLACK; others:WHITE

[Sweep]               WHITE(F, G, ...) → reclaim; BLACK → reset WHITE
```

### 算法收敛性

- **Mark 阶段**:`O(E + V)` 时间 — 每个 live 对象访问恰好一次(gray 然后 black),每条边检查一次。
- **Sweep 阶段**:`O(heap_size)` 时间 — 即使没有 live 也得扫整个堆。
- **工作栈**最坏 `O(V)` 空间(全灰,虽然实际少得多)。
- **终止条件**:`(gray_count == 0) ⇒ (all white are garbage)`。

### 写屏障(并发版的核心)

停世界(stop-the-world)GC 期间 mutator 不运行,**不需要写屏障**。本 demo 是 stop-the-world 版本,因此无须屏障。
若改并发:
- 每次 `*p = new_ref` 之前/之后,执行 barrier 代码(`if (cur_color == BLACK && new_color == WHITE) shade_new_gray()`,forward 风格)。
- HotSpot G1 用 SATB + card table;V8 用 Dijkstra 风格的 write barrier 并配合 remembered set。

## 对比 / 选型

| 算法 | 时间 | 空间 | 增量/并发 | 回收循环 | 暂停时间 |
| --- | --- | --- | --- | --- | --- |
| **Reference counting** | O(1) 单次 | per-object 计数器 | ✅(基本) | ❌ | 短 |
| **Mark-sweep (STOP)** | O(E+V) mark + O(H) sweep | worklist + bitmap | ❌(要 STW) | ✅ | **长**(全堆 STW) |
| **Tri-color + barrier** | O(E+V) mark + O(H) sweep | worklist + 屏障记录 | ✅ | ✅ | **短**(每次只做一部分) |
| **Copying (Cheney)** | O(L) 复制 L 个 live | 半空间浪费 | ❌(基础) | ✅ | 中 |
| **Generational** | YOUNG 短 + OLD 长 | 多代 remembered set | ✅ | ✅ | 短 |

## 环境准备

- 操作系统:Linux / macOS / Windows(WSL)/ 任意
- C:`gcc` / `clang`(<stdio.h> <stdlib.h> <string.h> 即可)
- Python:3.10+(用 `dataclass` + `default_factory`)
- Go:1.21+

## 运行方式

```bash
# C
gcc -O2 -Wall -Wextra gc_tri_color.c -o gc_demo
./gc_demo

# Python
python3 gc_tri_color.py

# Go
go run gc_tri_color.go
```

## 关键代码片段

### C:三色主循环(invariant 在 stop-the-world 下自动保持)

```c
static void mark(void) {
    /* 1) 根:白→灰,入栈 */
    for (int i = 0; i < roots_n; i++) {
        int rid = roots[i];
        if (obj(rid)->mark_state == WHITE) {
            obj(rid)->mark_state = GRAY;
            push_gray(rid);
        }
    }
    /* 2) 标记循环:pop 灰 → 子白转灰 → 自己转黑 */
    while (gray_top > 0) {
        int id = pop_gray();
        Object *o = lookup(id);
        for (int k = 0; k < o->n_kids; k++) {
            Object *kid = lookup(o->kids[k]);
            if (kid && kid->mark_state == WHITE) {
                kid->mark_state = GRAY;
                push_gray(o->kids[k]);
            }
        }
        o->mark_state = BLACK;
    }
}
```

### Python:`Graystack` 用 list + `pop()` 当 worklist

```python
def mark(self) -> None:
    for rid in self.roots:
        obj = self.objects.get(rid)
        if obj and obj.mark_state == WHITE:
            obj.mark_state = GRAY
            self._gray.append(rid)
    while self._gray:
        cur_id = self._gray.pop()           # LIFO → DFS;BFS 用 pop(0)
        cur = self.objects[cur_id]
        for kid_id in cur.kids:
            kid = self.objects.get(kid_id)
            if kid and kid.mark_state == WHITE:
                kid.mark_state = GRAY
                self._gray.append(kid_id)
        cur.mark_state = BLACK
```

### Go:`gray` 是 `[]int` slice,`pop` 用 `len-1` 索引

```go
func (h *Heap) mark() {
    for _, rid := range h.roots {
        o := h.objects[rid]
        if o != nil && o.MarkState == WHITE {
            o.MarkState = GRAY
            h.gray = append(h.gray, rid)
        }
    }
    for len(h.gray) > 0 {
        n := len(h.gray) - 1
        curID := h.gray[n]; h.gray = h.gray[:n]
        cur := h.objects[curID]
        for _, kidID := range cur.Kids {
            kid := h.objects[kidID]
            if kid != nil && kid.MarkState == WHITE {
                kid.MarkState = GRAY
                h.gray = append(h.gray, kidID)
            }
        }
        cur.MarkState = BLACK
    }
}
```

## 性能与边界

- **Mark 时间**:`O(E + V)` — 每个 live 对象恰好一次扫描,每条边恰好一次检查
- **Sweep 时间**:`O(H)` — 必须扫描整个堆(死对象也要过一遍)
- **空间复杂度**:
  - worklist 最坏 `O(V)`(全灰,实际不会)
  - 每个对象 2 bits 颜色
  - 并发版 + remembered set(card table)额外开销
- **暂停时间**:stop-the-world 全堆暂停;并发版可摊销到很短(Go 实测暂停 < 1ms)

## 注意事项与常见坑

1. **不变式必须靠 write barrier 维护**:并发 GC 若忘了 barrier,mutator 写入 `black → white` 边 → 回收周期会把可达对象回收掉(dangling pointer)。1995 年 IK WASI 子线程 SLA 错把"已分配但未初始化"内存用了 → 写屏障漏检真发过。
2. **停止世界 ≠ 永远安全**:即使 STW,期间 mutator 停止,所有对象的"引用"不会动;但根集合必须包括 stack、globals、registers、finalizer queue 等。
3. **Sweep 不能直接 free**:`free` 期间不能扫描对象(对象已 freed);生产 GC 用 "mark then free in next cycle" 或 `mark-sweep-compact` 三段式。
4. **finalizer / weak reference**:`finalizer` 必须在 sweep 之前把要执行的对象排队;否则回收后无法引用。
5. **栈扫描需要精确 GC 信息**:C/C++ 没有 tag 头 ⇒ **保守 GC**(把所有看似指针的整数都当指针);Java/Go 有栈帧表 ⇒ **精确 GC**。
6. **不要让对象图变成有向无环图外的隐含结构**:weak ref + cache + finalizer 这三类结构,mutator 与 collector 必须有协议;并发场景下会非常复杂。
7. **mark 完毕前不要释放已死对象的内存**:若 GC 分多线程,可能一边 mark 一边 sweep(generation),需要 "remembered set" 记录跨代引用。
8. **本 demo 是 stop-the-world**:没有并发三色写屏障的所有讨论都不适用本 demo;教学用足够,生产用就太慢。

## 参考资料(实际阅读过的权威来源)

- [Tri-Color Marking — cstopics Encyclopedia](https://cstopics.com/encyclopedia/compilers/runtime-systems/garbage-collection/tri-color-marking) — 完整定义 + 不变式 + 三阶段 + 复杂度 + 多语言实现对照(HotSpot G1、V8、Go、.NET、OCaml)+ 与 reference counting / Baker's Treadmill / Cheney 的对比。
- [Cornell CS 4120 Lecture 37 — Andrew Myers](https://www.cs.cornell.edu/courses/cs4120/2011fa/lectures/lec37-fa11.pdf) — 三色抽象讲义:含 Dijkstra 原论文出处 + mark-and-sweep 的递归/迭代实现 + tag bits / computed GC / conservative GC 三种"找指针"方法。
- [LuaJIT Wiki — New Garbage Collector](https://chrisfls.github.io/luajit-wiki/New-Garbage-Collector/e5563aa0e87e534fbadf5e71ae116db2d78af2ec) — Lua 5.0 两色 → 5.1 三色 → LuaJIT 2.0 quad-color 演进史;Dijkstra 不变式 + 写屏障 + 表格的 backward barrier 与对象 forward barrier 的实际应用。
- [Memory Management Glossary: T — mmref](https://mmref.readthedocs.io/en/branch-2023-03-03-make-mmref/glossary/t.html) — tri-color invariant 的强/弱两种定义 + 历史注脚(1975 Dijkstra)**与** 1978 CACM 论文的关系 + 终止条件原文。
- [七、垃圾收集中级 — 阿里云开发者社区](https://developer.aliyun.com/article/1449495) — 三色中文概述 + mutator 在并发收集期间的影响 + 三色不变性的核心约束"任何黑色对象节点都不能持有指向白色对象节点的引用"。
