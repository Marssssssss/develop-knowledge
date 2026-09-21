# per-CPU 分配与 NUMA 感知分配

> 目录：`03-系统编程/03-内存管理/分配器/percpu-numa/`
> 语言：Python（`python/main.py` + `python/selfcheck_numa.py`，**49 断言实跑全绿**）/ Go（`go/numa.go` + `go/main.go` 人工审查）

## 一、简介

现代分配器与内核都在做同一件事：**让内存尽量分配在「马上要访问它的那个 CPU」旁边**。
本 demo 把这件事拆成两层：

1. **per-CPU 层**：每个 CPU 一份数据，避免锁与 cacheline bouncing
   （Linux `this_cpu_ops`、jemalloc `percpu_arena`、TCMalloc per-CPU slab）；
2. **NUMA 层**：页最终落在哪个节点（`mbind(2)` / `set_mempolicy(2)` 的七种策略）。

## 二、原理

### 2.1 per-CPU 变量：带不带抢占保护，是两回事

`Documentation/core-api/this_cpu_ops` 的原文要点：

| 接口 | 保护 | 误用后果 |
| --- | --- | --- |
| `this_cpu_read/write/add/…` | **隐含**关抢占/中断 | 安全，但每条都有开关抢占的开销 |
| `__this_cpu_read/write/add/…` | 调用方必须自行保证 | RMW 之间若被抢占并迁移，更新落到**错误 CPU 的副本**上 |

本 demo 的对照实验（初值 `v = [5, 0]`）：

```
__this_cpu_add(0, 1)  中途被抢占迁到 cpu1  ->  v = [5, 6], total = 11   (应为 6)
this_cpu_add(0, 1)                        ->  v = [6, 0], total = 6
```

另一组对照是 **cacheline bouncing**：4 个 CPU 轮流加共享计数器，12 次写入产生 **11 次失效**；
换成 per-CPU 计数器后 **0 次失效**，汇总值同样是 12。

### 2.2 jemalloc 的 arena 绑定

`opt.percpu_arena` 决定「线程当前所在 CPU」如何映射到 arena：

| 取值 | arena 数 | 映射 |
| --- | --- | --- |
| `disabled`（默认） | `4 × CPU` | `cpu % narenas` |
| `percpu` | CPU 数 | `arena == cpu` |
| `phycpu` | 物理核数 | `cpu / threads_per_core`（同核两个超线程共享） |

关键行为：**绑定是动态的**——线程从 cpu3 迁到 cpu5，`percpu` 模式下的 arena 会跟着变（3 → 5）；
而 `phycpu` 模式下从 cpu2 迁到 cpu3 仍在同一个物理核上，arena 保持 1 不变。

### 2.3 NUMA 内存策略（`mbind(2)` 原文语义）

| 策略 | 语义 | 越界时 |
| --- | --- | --- |
| `MPOL_DEFAULT` | 落到线程策略；线程也是 DEFAULT 时用**系统级默认** = 触发分配的 CPU 所在节点 | — |
| `MPOL_BIND` | 严格限制在 nodemask 内；多节点时取**有足够空闲的最近节点** | **ENOMEM** |
| `MPOL_INTERLEAVE` | 逐页轮转，**为带宽而非延迟**优化 | — |
| `MPOL_WEIGHTED_INTERLEAVE`（6.9+） | 按 `/sys/kernel/mm/mempolicy/weighted_interleave/nodeN` 的权重分配 | — |
| `MPOL_PREFERRED` | 首选节点，掩码含多个时取**第一个**；空掩码 = 本地 | 回退到其它节点 |
| `MPOL_PREFERRED_MANY`（5.15+） | 候选节点集合 | 回退 |
| `MPOL_LOCAL`（3.8+） | 本地分配 | — |

> **版本差异（易写反）**：Linux 2.6.26 之前 `MPOL_BIND` 是「从最小节点号开始、装满再换下一个」；
> 之后改为「取有足够空闲内存的**最近**节点」。本 demo 给出对照断言：
> 在 node2 上触发、`nodemask = {0,1}` 时，现代语义选 **node1**（更近），旧语义会选 node0。

**mbind flags**：

| flag | 行为 |
| --- | --- |
| `MPOL_MF_STRICT` | 已有页不在目标节点 → **EIO** |
| `MPOL_MF_MOVE` | 只搬**本进程独占**的页；共享页原地不动 |
| `MPOL_MF_MOVE_ALL` | 连共享页一起搬，需要 **CAP_SYS_NICE**（否则 EPERM） |

**EINVAL 清单**（demo 逐条断言）：
`MPOL_DEFAULT` + 非空掩码；`MPOL_BIND`/`MPOL_INTERLEAVE` + 空掩码；
非 `MPOL_BIND` 模式带 `MPOL_F_NUMA_BALANCING`；同时指定 `MPOL_F_STATIC_NODES` 与 `MPOL_F_RELATIVE_NODES`。

### 2.4 交错的代价

原文提醒两点：交错区域**至少 1 MB** 才有效（更小则看不出收益）；
且**对单个页的访问仍受限于单个节点的带宽**。
延迟模型下（本地 100 ns、每跳 60 ns）：4 页全本地 = 400 ns，交错到 `{0,2}` = 640 ns。

## 三、对比

| | 共享计数器 | per-CPU 计数器 |
| --- | --- | --- |
| 正确性 | 需要原子指令 | 天然正确（无并发写同一份） |
| cacheline 失效 | 与写入次数同阶（12 次写 → 11 次） | **0** |
| 汇总代价 | O(1) | O(CPU 数) |
| 适用场景 | 需要精确全局值的场合 | 统计计数（分配次数、包数、错误数） |

## 四、环境

- Python ≥ 3.9 / Go 1.20+（Go 无工具链时走人工审查）
- 无第三方依赖

## 五、运行

```bash
cd python && python main.py            # 打印 cpu->node、三种策略的落点、权重分布
cd python && python selfcheck_numa.py  # 49 条断言
cd go     && go run .                  # Go 镜像
```

## 六、关键代码

```python
def unsafe_this_cpu_add(self, cpu, delta=1, migrate_to=None):
    tmp = self.v[cpu]                       # read（在旧 CPU 上）
    if migrate_to is None:
        self.v[cpu] = tmp + delta
        return
    self.v[migrate_to] = tmp + delta        # write 落到新 CPU 的副本上
```

```python
if mode == MPOL_BIND:
    cands = [n for n in mask if self.node(n).free >= 1]
    if not cands:
        raise PolicyError("ENOMEM", ...)
    best = min(cands, key=lambda n: self.dist[(local, n)])   # 最近的，不是最小的
    self.node(best).take(1)
```

## 七、性能与边界

- **first-touch 是默认行为**：页落在**首次触碰它的那个 CPU** 所属节点，不是"哪个线程 malloc 的"。
  因此初始化线程跑在 node0 上、工作线程跑在 node1，会得到一堆远程访问。
- **`MPOL_BIND` 是硬约束**：nodemask 内无空闲就失败，不会偷偷用别的节点；
  `MPOL_PREFERRED` 才会回退。
- **`MPOL_MF_MOVE` 只搬独占页**：多进程共享的映射不会被动，想全搬得用 `MPOL_MF_MOVE_ALL` + `CAP_SYS_NICE`。
- **per-CPU 的代价是汇总**：读全局值要遍历所有 CPU 的副本，O(CPU)；
  且副本数随 CPU 数增长（TCMalloc 的 per-CPU 缓存总量就随 CPU 数线性增长）。

## 八、坑

1. **「最近节点」≠「最小节点号」** —— 2.6.26 改过语义，写老代码对照时容易搞反。
2. **`MPOL_PREFERRED` 的掩码里有多个节点时只取第一个**，其余节点**不是**候选（要候选集用 `PREFERRED_MANY`）。
3. **`__this_cpu_*` 丢更新是"静默"的** —— 结果类型正确、值错了，必须在无抢占上下文里用。
4. **`phycpu` 不检测超线程是否真的开启**（jemalloc 原文明确说明），在关了 HT 的机器上会浪费一半 arena。
5. **交错策略不改善单页带宽** —— 原文：单个页的访问仍受限于它所在的那一个节点。
6. **本 demo 的节点距离用 `|i-j|` 跳数近似**，真实拓扑要看 `/sys/devices/system/node/node*/distance`。

## 九、参考资料（实际读过）

- Linux man-pages `mbind(2)`（七种 mode、四种 flag、EINVAL/EIO/EPERM 清单、2.6.26 语义变更、
  interleave 的 1 MB 建议与单页带宽限制）
  — <https://man7.org/linux/man-pages/man2/mbind.2.html>
- Linux man-pages `set_mempolicy(2)`、`numa(7)`（NUMA 系统调用总览、`/proc/pid/numa_maps` 字段）
  — <https://man7.org/linux/man-pages/man2/set_mempolicy.2.html>
  — <https://man7.org/linux/man-pages/man7/numa.7.html>
- Linux `Documentation/core-api/this_cpu_ops`（`this_cpu_*` 自带抢占保护 vs `__this_cpu_*`、
  per-cpu 偏移寻址、cacheline bouncing）
  — <https://www.kernel.org/doc/html/latest/core-api/this_cpu_ops.html>
- jemalloc(3) `opt.percpu_arena` / `opt.narenas`（disabled / percpu / phycpu，4×CPU 默认）
  — <https://jemalloc.net/jemalloc.3.html>
- TCMalloc Design Doc（per-CPU slab 与 `MaxPerCpuCacheSize` 随 CPU 数增长）
  — <https://google.github.io/tcmalloc/design.html>
