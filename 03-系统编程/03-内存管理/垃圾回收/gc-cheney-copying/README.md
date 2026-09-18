# Cheney 半空间复制式 GC — BFS 复制、转发指针与碎片消除

## 简介

**Cheney 算法**（C. J. Cheney, *A Nonrecursive List Compacting Algorithm*, CACM 1970）是**复制式垃圾回收**的经典实现：把堆对半分成两个**半空间**（semispace），一次回收只从 `from-space` 把**存活对象**复制到 `to-space`，然后把两个半空间的角色互换。

它有三个漂亮的性质：

1. **不需要递归栈**：待扫描队列被直接编码成 `to-space` 上的 `[scan, alloc)` 区间。
2. **复制即紧凑**：`to-space` 里的对象首尾相接，碎片率恒为 0。
3. **成本只与存活对象数成正比**：死亡对象根本不进 `to-space`，"其余分配自动成为隐式垃圾"（V8 原文的 *every other allocation becomes 'implicit' garbage*）。

V8 的 young generation **Scavenger** 就是这个算法：v6.2 之前是单线程的 Cheney 半空间复制，之后改为并行的 Scavenger（用转发指针 + CAS 保证同一对象只被复制一次）。

## 原理详解

### 1. 两个半空间与三个指针

```text
回收前:  from-space: [A][garbage][B][C][garbage][D]        to-space: (空)
          roots: r1 -> A

① 扫根   A 未复制 -> 复制到 to-space 偏移 0,写转发表,并**就地改写根槽**指向新 A'
② 扫 to  [A'] 的字段 -> 复制 B、C
③ 扫 to  [B'] 的字段 -> 复制 D;扫 [C'] 的字段 -> D 已在转发表,只改引用
④ 扫 to  [D'] 的字段 -> 复制 E
⑤ scan == alloc,结束

回收后:  to-space: [A'][B'][C'][D'][E'] (连续,零碎片)
          from-space 整片丢弃(全是垃圾)
```

三个指针/结构：

| 名称 | 作用 |
| --- | --- |
| `alloc`（free pointer） | `to-space` 的下一个空闲地址；复制一个对象就前进其大小 |
| `scan` | "已扫描 / 未扫描"分界点；`[scan, alloc)` 就是待扫描队列 |
| **转发指针**（forwarding pointer） | 原对象处留下的"已搬迁到哪"的标记，使重复引用只复制一次 |

`scan` 一个对象 = 把它的所有指针字段**逐一 evacuate**（复制或改写成转发地址）。这个循环天然是 **BFS**（层序）：根算第 0 层，先复制的对象其字段先被 scan。

### 2. 为什么不需要递归栈

朴素复制式 GC 用递归/显式栈来遍历对象图，栈深与图深度成正比。Cheney 的洞察是：**`to-space` 本身就是队列**。`alloc` 指向队尾，`scan` 指向队头，扫描过的对象正好把 `[0, scan)` 用掉、`[scan, alloc)` 是待办。所以额外空间是 **O(1)**（一个转发字 + 两个指针）。

### 3. BFS 顺序带来的是"层序布局"

复制顺序严格等于 BFS 展开顺序（本 demo 用 `A→{B,C}, B→D, C→D, D→E` 断言得到 `A,B,C,D,E`）。这有两层含义：

- **好的一面**：同一层的对象（常互相引用）落在相邻地址，遍历时命中同一批页。
- **代价**：对象的原地址顺序被打乱，跨代/跨区域的引用变多。所以"压缩式"（保序）与"复制式"（层序）是两种不同的取舍。

### 4. 空间代价：可用率上界 50%

两个半空间都要 **commit**（V8 原文：*both semispace halves of memory are committed*），但任一时刻只有一半在分配。于是：

- 物理内存有效利用率 = 存活量 / (2 × 堆)，**恒等于存活率的一半**；
- 对照标记-清除：只要 1× 堆 + 标记位（按平均 8 字/对象算，1 bit/对象 ≈ 堆的 1/64）。

也就是说，复制式用**64 倍的元数据开销**换来"零碎片 + 成本 ∝ 存活"。

### 5. 成本模型

| 存活率 | 复制量（∝ 存活） | 清扫量（∝ 堆） |
| --- | --- | --- |
| 1% | 1,000 字 | 100,000 字 |
| 10% | 10,000 字 | 100,000 字 |
| 50% | 50,000 字 | 100,000 字 |
| 100% | 100,000 字 | 100,000 字 |

（堆固定 100,000 字）存活率低时复制式有压倒性优势；存活率接近 100% 时两者持平，而复制式还要额外承担"改写所有引用"的代价。这正是**弱分代假说**（大多数对象很快死亡）能成为现代 GC 基石的原因。

### 6. 移动式 GC 的两个硬前提

**前提一：所有引用必须可枚举、且可就地改写（精确根）。** 只要有一个引用藏在 GC 看不到的地方（例如被当作整数保存），复制完成后它就指向已经作废的 `from-space`。这就是为什么**保守式 GC 不能移动对象** —— 它把"看起来像指针的整数"当成指针，无法区分真指针与巧合值。

**前提二：转发指针让并行复制安全。** 两个 worker 可能经由不同路径同时到达同一对象。没有转发指针时，两边各复制一份，对象就**分裂**成两个副本（引用比较、`==` 语义全毁）。V8 的并行 Scavenger 正是用转发指针 + 原子 CAS 解决：谁先成功写入转发指针谁负责搬，另一个拿到现成的转发地址。转发指针在这里同时充当"已搬迁"标记，省掉单独的 mark 位。

## 环境准备

- Python 3.10+（模型版，跨平台可跑）
- Go 1.21+（模型版，跨平台可跑）

## 运行方式

```bash
python3 python/main.py    # 59 个断言,跨平台
go run ./go               # 同一模型的 Go 版
```

## 关键代码片段

### evacuate：复制或改写引用（转发指针命中即短路）

```python
def evacuate(slot):
    old = slot.val
    if old is None:
        return
    if old in forward:                 # 已搬迁:只改引用,不再复制
        slot.val = forward[old]
        return
    o = src.objs[old]
    new = Obj(next_oid[0], o.payload_words)
    next_oid[0] += 1
    to_space.append((alloc[0], new))   # 追加到 to-space 队尾
    forward[old] = new.oid
    slot.val = new.oid                 # 就地改写引用
    for f in o.fields:
        new.slot(f.name).val = f.val
    alloc[0] += o.words()
```

### scan/alloc 双指针即"无栈遍历"

```python
while scan[0] < alloc[0]:
    cur = 找到 to_space 中偏移 == scan 的对象
    for f in cur.fields:
        evacuate(f)        # 可能把 alloc 继续往后推
    scan[0] += cur.words() # 队头前进
```

### 并行版：转发指针做"只复制一次"的仲裁

```go
if useForward {
    if v, ok := fwd[oid]; ok { return v }   // 别人已经搬过
    fwd[oid] = "D@" + worker
}
```

## 性能与边界

| 维度 | 数值 |
| --- | --- |
| 时间复杂度 | 复制 O(存活字数)；改写引用 O(存活对象的字段数) |
| 额外空间 | O(1)（转发字 + scan/alloc 两个指针），但**堆本身要 2×** |
| 碎片 | 恒为 0（to-space 首尾相接） |
| 布局 | BFS 层序（不是原地址序） |
| 最坏情况 | 全部存活 → 搬整个堆，且 to-space 可能装不下（需 promote/回退） |
| 停止世界 | 基础版本必须 STW；V8 的并行 Scavenger 把根扫描与复制并行化 |

## 注意事项与常见坑

1. **`scan` 指针必须落在对象边界上**：`to-space` 里对象大小不一，`scan` 只能按"当前对象的大小"整数步进。模型里用"找偏移等于 scan 的对象"实现，若换成固定步长就会错位。
2. **"复制顺序 = BFS"是断言的好靶子**：初版断言直接比较新 `oid` 列表与旧名字列表，必然失败（新对象已经换了身份）。正确做法是先用转发表把新 `oid` 反查回旧名字再比较 —— **这是"期望值自己没算"的典型**。
3. **转发指针命中不等于"又复制了一次"**：命中时 `alloc` 不该前进。若把"命中"也当成一次复制，`to-space` 会凭空多出副本，而且对象首尾相接的断言还能过（因为多出来的副本也是连续的）。
4. **不要用递归实现**：一旦改递归，就失去了 Cheney 的全部意义（O(1) 额外空间），而且深度受限于栈。
5. **半空间浪费并不是"实现不好"**：它是算法的定义性代价。想省掉这 50%，就得转向压缩式（保序、无额外半区）或分代式（只对 young 用半空间）。
6. **保守式 GC 与移动式互斥**：见到"把 `int` 当指针扫描的保守 GC"就别指望它移动对象。
7. **`from-space` 的整片丢弃是它的最大优势**：不需要"清扫"、不需要遍历死对象、不需要维护空闲链表。凡是"存活率低"的场景，复制式的常数因子几乎总是最优。
8. **教学简化已标明**：模型按"字"计账（1 字 = 一个指针槽），不做真实字节布局与页管理；`to-space` 用 Python 列表承载，真实实现是连续内存 + 页。

## 参考资料（实际联网阅读过）

- [Trash talk: the Orinoco garbage collector — V8](https://v8.dev/blog/trash-talk) — young generation 的 "nursery / intermediate" 两级、Scavenger 只收集 young、**"half of the total space is always empty"** 的半空间设计与 from-space/to-space 命名、**"During a scavenge, this initially-empty area is called 'To-Space'. The area we copy from is called 'From-Space'"**、**"Every copied object leaves a forwarding-address which is used to update the original pointer to point to the new location"**、以及 **"In scavenging we actually do these three steps — marking, evacuating, and pointer-updating — all interleaved, rather than in distinct phases"**、old-to-new 引用由写屏障 + remembered set 维护。
- [Orinoco: young generation garbage collection — V8](https://v8.dev/blog/orinoco-parallel-scavenger) — 明写 **"V8 used a Cheney semispace copying garbage collector"**；"both semispace halves of memory are committed and assigned proper labels"；单线程 Cheney 的根扫描 → 复制 → 扫描 to-space →（对象跨两次则 promote）流程；并行版用**转发指针 + 原子 CAS** 保证对象只被搬迁一次；每页维护 remembered set。
- 说明：Cheney 1970 年原论文 *A Nonrecursive List Compacting Algorithm*（CACM 13(11)）为付费资源，本 demo 未直接引用原文，其算法细节依据 V8 第一方工程博客对 Cheney 半空间复制的完整描述复现。
