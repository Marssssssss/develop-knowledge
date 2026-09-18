# 分代式 GC(generational GC)

> 领域：`03-系统编程 / 03-内存管理 / 垃圾回收` ｜ 语言：Python · Go
> 一句话：靠"**大多数对象死得早**"这条经验规律，只收新生代就能回收绝大部分垃圾 ——
> 代价是必须用**写屏障 + 记忆集**把"老年代指向新生代"的引用补成根。

## 1. 简介

Oracle 把动机说得最清楚：全堆算法 "**time this approach takes is proportional to the number
of live objects, which is prohibitive for large applications maintaining lots of live data**"。
分代就是用"新生代总是很小"换掉"存活集总是很大"。

| 策略 | 每次收集扫什么 | 适用 |
|---|---|---|
| 全堆收集（non-generational） | 全部存活对象 | 存活集小 |
| **分代 minor** | **只扫新生代 + 记忆集** | 存活集大、多数对象短命 |
| **分代 major** | 整个堆 | 老年代也满了 |

本 demo：**①** eden + 双 survivor + 老年代的堆模型，跑通 minor 的复制与晋升；**②** 演示
**写屏障/记忆集**是分代的**正确性前提**（少了它会造出悬空引用）；**③** 量化晋升阈值与
minor/major 的成本差；**④** 逐条对照 **CPython 三代 GC**（`Python/gc.c`），包括那条很少被
注意到的 **25% long_lived 启发式**。

## 2. 原理详解

### 2.1 弱分代假说

> the **weak generational hypothesis**, which states that **most objects survive for only a
> short period of time**. … Efficient collection is made possible by focusing on the fact
> that a majority of objects "die young."

经验分布是"左侧尖峰（立刻死掉的临时对象）+ 右侧长尾（活到进程结束的初始化对象）"。
**尖峰越高，分代越划算。**

### 2.2 两代结构、minor 与 major

> The young generation consists of **eden and two survivor spaces**. … One survivor space is
> empty at any time, and serves as the destination of any live objects in eden … until they
> are **old enough to be tenured**.

- **eden 满 → minor**：只收新生代。存活者被**复制**到空的那个 survivor，**年龄 +1**；
  两块 survivor 交替充当 from / to。用复制是因为"新生代里活着的是少数"——
  **复制成本 ∝ 存活数**，而标记-清除的成本 ∝ 整代大小。
- 年龄达到 **tenuring threshold** → 复制进**老年代**（晋升）。
- **老年代满 → major**：整个堆。"Major collections usually last much longer than minor
  collections because a significantly larger number of objects are involved."

demo1 拆开一次 minor：`A→B` 被复制进 survivor、4 个临时对象被整块丢弃、第二次 minor 后
年龄到 2 触发晋升、survivor 半区轮换。

### 2.3 写屏障与记忆集：正确性前提

minor 只扫新生代，但**老年代对象可能指向新生代对象**。这种引用若不被当成根，那个新生代对象
就会被回收掉，而老年代里还留着指向它的指针 —— **悬空引用**。V8 的说法：**Old-to-young
generation references are roots for the young generation garbage collection. These
references are recorded to provide efficient root identification and reference updates when
objects are moved.**

于是每次"把指针写进字段"都要过一道 **写屏障**：若 `owner` 在老年代、`target` 在新生代，
就把这个槽记进**记忆集 / 脏卡**。minor 的根集 = `mutator 根 ∪ 记忆集`。demo2 用对照把这件事
钉死（同一份赋值，一次带屏障、一次裸写）：

| | 脏卡/记忆集 | minor 之后 | 结果 |
|---|---|---|---|
| 带屏障 | 1 条 | `C` 被当作额外根复制，存活 | 正确 |
| 裸写 | 空 | `C` 不在任何空间里 | **`O.child` 悬空** |

demo2 还有反证：**major（全堆）收集不需要记忆集** —— 整堆都在扫描范围内，老年代对象本身
就在标记队列里，关掉屏障也不会漏。记忆集只是"只看新生代"的补丁。

两个容易漏的细节，本 demo 都实现并断言了：**① 晋升会"新造"老→新引用**（对象进老年代时它
自己的字段可能还指着新生代），必须顺手补进记忆集；**② 死亡的新生代对象要从记忆集里清掉**，
否则老年代那个槽会一直指着已回收的对象。

### 2.4 晋升阈值（MaxTenuringThreshold）

demo3 实测（"复制次数"= 晋升前在新生代内被搬动几次）：

| 阈值 | 晋升前复制次数 | survivor 峰值占用 | 老年代压力 |
|---|---|---|---|
| 1 | 1 | 最小 | 最大 |
| 2 | 2 | 中 | 中 |
| 3 | 3 | 最大 | 最小 |

**阈值低省新生代搬运、费老年代容量**，阈值高反之。没有普适最优值。

### 2.5 minor vs major 的成本（demo4 实测）

堆预算 `nursery 12 字 + 老年代 2400 字`；先建 **2000 字长期存活集**，再产生 **4000 字
一次性临时对象**。两边**只差"分代"这一个开关**（同一负载、同一堆预算、同一根集），
实测**被扫描字数**：**分代 4008 字**（全在 nursery 里扫，老年代那 2000 字一次都没碰）
vs **基线 21708 字**（每次全堆收集都要走过那 2000 字从不死的存活集），**比值 5.4x**。

收益量级 ≈ `堆预算 / 收集间隙产生的垃圾量`，即"**存活集占堆的比例越高，分代越赚**"。

> 口径说明：上面只统计 **churn 阶段**。预热期（建起 2000 字存活集并逐次晋升）分代花了 3988
> 字、基线花 0 字 —— 晋升要复制是有代价的，这段没算进对比。真实收集器还会有偶尔的 major；
> 本实验故意把老年代配大（2400 > 2000）以**隔离出 minor 这一项**。

### 2.6 CPython 的三代 GC（demo5 逐条对照）

Python 的 `gc` 管的是**引用环**（引用计数处理不了的垃圾），它也用分代：

> The GC classifies objects into **three generations** … New objects are placed in the
> youngest generation (**generation 0**). If an object survives a collection it is moved
> into the next older generation. Since generation 2 is the oldest generation, objects in
> that generation remain there after a collection. … When the number of **allocations minus
> the number of deallocations exceeds threshold0**, collection starts. **Initially only
> generation 0 is examined. If generation 0 has been examined more than threshold1 times
> since generation 1 has been examined, then generation 1 is examined as well.**

模型逐条复现 `Python/gc.c` 的行为：`record_allocation` 只增 `generations[0].count`；
释放是 `if (count > 0) count--;`；一次收集后 `generations[generation+1].count += 1` 再把
`0..generation` 清零；`gc_select_generation()` 从最老一代往下找第一个 `count > threshold`
的代号，**但若它就是最老一代、且 `long_lived_pending < long_lived_total / 4` 就跳过**；
晋升时 `if (generation == NUM_GENERATIONS - 2) long_lived_pending += gc_list_size(young)`，
收到最老一代时 `long_lived_pending = 0; long_lived_total = gc_list_size(young)`；
`gc.collect(gen)` 的返回值是 **"collected + uncollectable" 之和**。

那条 25% 启发式的注释（issue #4074）说它是为了达到**摊还线性**性能：
"each full garbage collection is more and more costly as the number of objects grows,
**but we do fewer and fewer of them**" —— 否则"每固定次数分配就做一次全量收集"会让
"创建并长期保存大量对象"的负载退化成二次复杂度。

### 2.7 默认阈值：版本差异（实测纠正）

| CPython | 默认阈值 | 出处 |
|---|---|---|
| 3.12 及更早 | `(700, 10, 10)` | `pycore_runtime_init.h` 里 `{ .threshold = 700 }` |
| **3.13 起** | **`(2000, 10, 10)`** | 同文件改成 `{ .threshold = 2000 }` |

实测：`python3 -c "import gc; print(gc.get_threshold())"` 在 **3.13.14 与 3.14.6 上都返回
`(2000, 10, 10)`**。凡是把默认值写成 700 的资料，都停留在 3.12 之前。

## 3. 环境与运行

无第三方依赖。`cd python && python3 main.py`（期望 `断言总数 79,失败 0 / 全部通过`）；
`cd go && go run .`。

- `python/gen_model.py`：堆模型（eden + 双 survivor + 老年代 + 写屏障/记忆集）；
  `python/cpy_gc.py`：CPython 三代判定模型；`python/demos.py` / `python/main.py`：demo1~3 / demo4~5 与入口。
- `go/gen_model.go` + `go/gen_collect.go`：同一模型的 Go 版（拆文件只为满足单文件 ≤300 行）；
  `go/cpy_gc.go`、`go/demos.go`、`go/demos2.go`：三代判定模型 / demo1~3 / demo4~5。

> 本机无 Go 工具链（`which go` 为空）：Go 侧按仓库惯例走**人工审查 + `bracket_check.py` +
> `syntax_sanity.py`**；Python 侧为实跑结果。**Go 没有 `gc.get_threshold()` 这类可实测的对应物**，
> 故 `gc` 模块的 6 条真实性断言只在 Python 版里，Go 版对应位置留了一行 `[info]` 说明。

## 4. 关键代码

```python
def store(self, owner, name, target):
    """带写屏障的存储:老 -> 新 引用必须进记忆集,否则 minor GC 会漏掉它。"""
    owner.fields[name] = target
    if self.barrier and target is not None and owner.gen == 1 and target.gen == 0:
        self.cards.add((owner.oid, name))

def step(self):                                    # 分配后的容量检查
    if self.generational:
        if self.young_words() >= self.young_capacity:
            self.minor()
    elif self.young_words() + self.old_words() >= self.heap_capacity:
        self.major()                               # 基线:只把"分代"这一层关掉
    if self.old_words() >= self.old_capacity:
        self.major()
```

## 5. 性能边界

- 收益 ∝ **存活集占堆的比例**；存活集很小（短命进程、批处理）时分代反而多花晋升的复制成本。
- **minor 的成本与老年代无关**（demo4 里 minor 只扫 1 字，major 要扫 32 字）——
  分代省的是**扫描量**，不是回收量。
- 记忆集不免费：**每次引用赋值都要过一次写屏障**（`store()` 就是那个代价）。卡表粒度越粗，
  记忆集越小，但 minor 要扫的"假阳性"区域越多；本 demo 粒度是"一个槽"，真实实现里是
  512 B 的卡或一页。
- 本 demo 是模型：对象在各空间间"搬动"用列表归属表示（同一对象换列表），**不模拟真实地址搬运**。

## 6. 注意事项与常见坑

1. **没有写屏障的分代 GC 是错的**：不是性能退化，而是会**回收掉仍然可达的对象**（demo2 的悬空三元组）。
2. **晋升要补记忆集**：晋升那一刻新造出的老→新引用同样会被 minor 漏掉。
3. **死亡对象要从记忆集里清掉**，否则记忆集越积越大，还留着指向已回收对象的槽。
4. **`gc.collect()` 返回的是 collected + uncollectable**，不是"发现多少不可达对象"；
   **`count` 不会变负**（`if (count > 0) count--;`），dealloc 多于 alloc 不会把阈值"欠账"掉。
5. **默认阈值是版本相关的**：3.13 起 `threshold0` 从 700 变 2000，别照抄老资料。
6. **"关掉一层"要关干净**：基线对照只能关掉被测的那一层（这里是"分代"），堆预算、负载、
   根集必须完全一致，否则省下来的倍数是假的。
7. **预热成本要单独说清**：分代在"把存活集建起来"的阶段**更贵**（不断复制晋升），
   对比收益时别把这段混进稳态成本。

## 7. 参考资料（实际联网阅读）

- Oracle《Java SE 8 HotSpot VM GC Tuning Guide》第 3 章 *Generations*（弱分代假说 / eden+双 survivor / minor 与 major）：<https://docs.oracle.com/javase/8/docs/technotes/guides/vm/gctuning/generations.html>
- V8 博客 *Orinoco: young generation garbage collection*（nursery / intermediate / old；老→新引用作为根）：<https://v8.dev/blog/orinoco-parallel-scavenger>
- Python 官方库文档 `gc`（三代、threshold0/1 语义、`collect()` 返回值、`get_stats()`）：<https://docs.python.org/3/library/gc.html>
- CPython `Python/gc.c`（`record_allocation` / `gc_collect_main` / `gc_select_generation` 的 25% 启发式）：<https://github.com/python/cpython/blob/main/Python/gc.c>
- CPython `Modules/gcmodule.c`、`Include/internal/pycore_gc.h`（`set_threshold`/`get_threshold`/`get_stats`；`struct gc_generation { head; threshold; count; }`）：<https://github.com/python/cpython/blob/main/Modules/gcmodule.c> ｜ <https://github.com/python/cpython/blob/main/Include/internal/pycore_gc.h>
- CPython `Include/internal/pycore_runtime_init.h`（默认阈值 3.13+ 为 `{ 2000, 10, 10 }`，3.12 为 700）：<https://github.com/python/cpython/blob/main/Include/internal/pycore_runtime_init.h>
