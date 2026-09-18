# 标记-压缩式 GC(mark-compact)

> 领域：`03-系统编程 / 03-内存管理 / 垃圾回收`
> 语言：Python · Go
> 一句话：把**存活对象搬成连续一整块**以消灭外部碎片，代价是**必须改写所有引用**。

## 1. 简介

标记-压缩（mark-compact）是 tracing GC 的第三种基本形态：

| 形态 | 回收动作 | 碎片 | 成本 |
|---|---|---|---|
| mark-sweep | 把死对象挂进空闲表 | **有**外部碎片 | 低 |
| mark-copy（Cheney） | 把活的半空间复制到另一半 | 无 | 空间 2× |
| **mark-compact** | 把活的**滑动**到低地址，空闲合并成单块 | 无 | **必须更新全部引用** |

本 demo 实现同一件事的两种经典做法：

1. **Lisp2 保序滑动**——多遍顺序扫描，靠一张 `old -> new` 转发表串起"算新地址"和"改引用"两个阶段。
2. **线程化（threading）压缩**——GHC `rts/sm/Compact.c` 的做法，把"谁指向我"这条链**藏在对象自己的 info 槽里**，解链时边走边算新地址，**额外空间 O(1)**。
3. 外加两组量化对比：**转发表 vs 链的空间开销**、**保序布局 vs 层序（Cheney）布局的局部性**，以及"漏更新一个引用"的后果演示。

## 2. 原理详解

### 2.1 权威定义（Memory Management Reference）

> mark-compact collection is a kind of tracing garbage collection that operates by
> marking reachable objects, then compacting the marked objects…
> **the compaction phase typically performs a number of sequential passes over memory
> to move objects and update references.** As a result of compaction, **all the marked
> objects are moved into a single contiguous block of memory** (or a small number of
> such blocks); the memory left unused after compaction is recycled.

> **Compaction is used to avoid external fragmentation and to increase locality of
> reference.**

三句话把设计要点全给出来了：**多遍顺序扫描**、**单块连续内存**、**顺带改善局部性**。

### 2.2 Lisp2 的三遍结构

```
pass1  mark        : 沿 roots 标记全部可达对象                -> live set
pass2  compute     : 按地址序累加前缀和,得到 fwd[oid] = 新地址 -> 转发表 + 新的 free
pass3  update+move : 改写每一个槽(含根),再把存活对象按 fwd 搬到低地址
```

关键约束：**pass2 必须先于 pass3 完成**，因为一个对象的新地址可能被任意多个槽引用，
而 `update` 阶段遇到任何一个旧地址都必须能查到它的新地址。转发表就是为此存在的 ——
代价是 `O(live)` 个表项（1 万存活对象 = 80 KB，demo3 量化）。

demo1 用夹具 `A | g1 | B | g2 | C | D`（引用链 `A→B→C→D`，根 = `A`）验证：
`g1`、`g2` 是 2 个空洞；压缩后 `A(3字) B(3) C(3) D(1)` 紧排在 `[0,10)`，
空闲 `[10,17)` 是**一整块**（`compactedLayout` 断言 `""` 块个数 == 1）。

### 2.3 线程化压缩：把链藏在对象身上

GHC `Compact.c` 头部注释就是全部设计：

> The basic idea here is to chain together all the fields pointing at a particular
> object, **with the root of the chain in the object's info table field. The original
> contents of the info pointer goes at the end of the chain.**
> Adding a new field to the chain is a matter of **swapping the contents of the field
> with the contents of the object's info table field**: `*field, **field = **field, field`

也就是说：

- **链节就是那个字段自己** —— 不额外分配节点，字段的原值被"交换"进 `/info`，
  于是一条 `A` 的引用链会以 `INFO(A)` 这个原 info 内容收尾。
- 解链 = 从 `/info` 出发往下走，**每遇到一个字段就写入对象的新地址**，
  新地址由 running free pointer 现算 —— 所以**完全不需要转发表**。
- 难点在**标记位**：roots 上的指针不带 tag，mutator 字段带 tag。GHC 的解法是把
  tag 编在**指向该槽的指针**上：
  > if the field is NOT tagged then we tag the pointer to the field with 1… If the
  > field is tagged then we tag to the pointer to it with 2.

本 demo 用同样的语义建模：`chainNode{field, tag}`，`tag ∈ {1, 2}`；
解链时 `s.tagged = (tag == 2)`。demo2 验证链长 == 指向该对象的槽数（3 = 2 个根 + 1 个字段）、
链尾 == `INFO(A)`、解链后 `info` 被还原、tag 位被保留。

### 2.4 局部性：保序 vs 层序

两者都消灭碎片，但**排列顺序**不同：

- **Lisp2 保序**：存活对象的相对原顺序不变 —— 对"按地址扫描"友好。
- **Cheney 层序**：复制顺序 = BFS 展开顺序 —— 对"按引用遍历"友好。

demo4 用一个引用图与地址序**故意错开**的算例量化：地址序 `A B C D E`，
引用序 `A→E→D→C→B`。按引用遍历时的地址跳跃：保序 = 20，层序 = 10（**恰好 2 倍**），
且层序下每步跳跃 == 前一对象自身字数（理想局部性）；反过来按地址扫描时保序 = 11 < 层序 = 18。
**没有绝对更优的排列，只有匹配访问模式的排列。**

### 2.5 阶段分解（V8）

`src/heap/mark-compact.h` 的 `CollectorState`（定义在 `#ifdef DEBUG` 下）把压缩拆成显式状态：

```
IDLE, PREPARE_GC, MARK_LIVE_OBJECTS, SWEEP_SPACES,
ENCODE_FORWARDING_ADDRESSES, UPDATE_POINTERS, RELOCATE_OBJECTS
```

并且 `are_map_pointers_encoded() { return state_ == UPDATE_POINTERS; }` ——
**"指针已被编码"是一个可观测的独立状态**。顺序不可互换：先编码转发地址 → 再改引用 → 最后搬对象。

## 3. 两种实现的对比

| 维度 | Lisp2 转发表 | 线程化（threading） |
|---|---|---|
| 额外空间 | `O(live)` 个表项（8 B/项） | **O(1)**（只有链上的 tag 位） |
| 是否复用对象自身的字 | 否 | **是**（字段与 info 槽换入换出） |
| 解链阶段需要映射表吗 | 需要 | **不需要** |
| 对象前提 | 无 | **① 每个对象要有可写槽 ② 指针必须精确** |
| 保守式扫描（Boehm 风格） | 可用 | **不可用**（指针不可靠，不能改写槽） |
| 变体 | — | break table：每连续段一个表项，介于两者之间 |

demo3 量化：1 万存活对象时 Lisp2 多花 80 KB，threading 多花 0 B，
break table（20 段）只需 160 B。

## 4. 环境与运行

无第三方依赖。

```bash
cd python && python3 main.py     # 期望: 断言总数 52,失败 0 / 全部通过
cd go     && go run .            # 期望: 断言总数 52,失败 0 / 全部通过
```

> 本机无 Go 工具链（`which go` 为空），Go 侧按仓库惯例走**人工审查 +
> `_docs/tools/bracket_check.py` + `_docs/tools/syntax_sanity.py`** 自检；
> Python 侧为实跑结果。

## 5. 关键代码

线程化入链 / 解链（`go/compact.go`，与 Python `thread_object` / `unthread_object` 一一对应）：

```go
func threadObject(o *object, refs []*slot) {
	for _, s := range refs {
		tag := 1
		if s.tagged { tag = 2 }
		old := o.info
		o.info = &chainNode{field: s, tag: tag}
		s.val = old          // GHC 的 *field, **field = **field, field
	}
}

func unthreadObject(o *object, newAddr string) int {
	updated := 0
	node := o.info
	for {
		cn, ok := node.(*chainNode)
		if !ok { break }
		next := cn.field.val // 先取出后继,再覆写字段
		cn.field.val = newAddr
		cn.field.tagged = cn.tag == 2
		node = next
		updated++
	}
	o.info = node            // 还原原 info 内容
	return updated
}
```

`slot.val` 必须是 `interface{}`：线程化期间它要**临时装下"后一个链节"**，
这正是"链藏在字段自身"的代价 —— 槽必须能容纳两种东西。

## 6. 性能边界

- **吞吐随存活率上升**：成本 ∝ 存活对象数，与总堆大小无关（demo 的空间对比即为此点）。
  这也是分代 GC 让老年代用 mark-compact 的原因。
- **暂停时间**：Lisp2 的 update 阶段必须扫完整堆的**全部槽**；
  线程化把它摊进解链遍历，但解链仍要遍历全部存活对象。
- **不复用对象内存**：GHC 的 `thread_obj` 还有把相同大小的对象**就地复用**的优化，
  本 demo 未建模。
- **本 demo 是模型而非真实堆**：地址是逻辑字偏移（`a0`、`a3`…），不是真实指针值。

## 7. 注意事项与常见坑

1. **"漏更新一个引用"就是悬空指针**：demo5 故意跳过 `A.toB` 这一个槽，
   它仍持有旧 oid `B`，不落在任何合法新地址上 —— 移动式 GC **必须精确、不可保守**。
2. **线程化的两个前提缺一不可**：可写槽 + 精确指针。保守式收集器（Boehm 风格）
   不敢改写"可能是指针"的字，因此不能移动对象。
3. **tag 要编在"指向槽的指针"上，而不是槽里**：槽被改写后 tag 会丢，
   所以 GHC 把它编在外层指针的最低两位。
4. **"链尾是原 info 内容"不是细节而是正确性要件**：解链走到底必须能判断"链结束"，
   否则会把 `INFO(A)` 当链节继续走。
5. **本 demo 的地址是逻辑字偏移**：不要拿它和真实进程地址比较。

## 8. 参考资料（实际联网阅读）

- Memory Management Reference — *mark-compact*：<https://www.memorymanagement.org/mmref/terminology.html#term-mark-compact>
- Memory Management Reference — *compaction*：<https://www.memorymanagement.org/mmref/terminology.html#term-compaction>
- GHC RTS `rts/sm/Compact.c`（线程化压缩权威实现，头部注释即设计说明）：
  <https://gitlab.haskell.org/ghc/ghc/-/blob/master/rts/sm/Compact.c>
- GHC 评论：RTS storage / GC — <https://gitlab.haskell.org/ghc/ghc/-/wikis/commentary/rts/storage/gc>
- V8 `src/heap/mark-compact.h`（`CollectorState`、`are_map_pointers_encoded`）：
  <https://chromium.googlesource.com/v8/v8/+/refs/heads/main/src/heap/mark-compact.h>
- V8 博客 *Orinoco: young generation garbage collection*（Mark-Evacuate 的 marking/copying/updating pointers 三阶段）：
  <https://v8.dev/blog/orinoco-parallel-scavenger>
- Oracle HotSpot GC 调优指南（parallel compaction）：
  <https://docs.oracle.com/javase/8/docs/technotes/guides/vm/gctuning/collectors.html>
