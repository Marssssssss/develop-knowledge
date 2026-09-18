# W-TinyLFU 准入策略（Caffeine 默认淘汰策略）

## 一、简介

Caffeine（Java 生态最常用的本地缓存）默认的淘汰策略是 **Window TinyLFU**。它要解决的不是「空间满了淘汰谁」，而是**「新条目到底该不该进来」**——即**准入（admission）**问题。

本 demo 实现 W-TinyLFU 的三块核心：**4-bit Count-Min Sketch 频率草图**、**窗口 LRU + 主区 SLRU**、**频率比较准入**，并用三个可复现的实验量化它与 LRU 的差距。

## 二、原理详解

### 2.1 为什么 LRU 不够

Caffeine wiki 的原话是：

> in typical workloads LRU is not optimal and may have a poor hit rate in cases like full scans

LRU 只记录「最近一次访问」，**不区分「访问过一次」和「访问过一百次」**。于是一次全表扫描就能把真正的热数据全部挤出去。

本 demo 的实测（`scan_pollution_trace`：100 个热条目预热 5 轮，然后插入 500 个一次性扫描条目，最后重新访问热集）：

| 策略 | 扫描后热集命中 |
| --- | --- |
| LRU | **0 / 100** |
| W-TinyLFU | **94 / 100** |

LRU 是 0，**一个不剩**。这不是「命中率下降」，而是缓存被彻底换血。

### 2.2 频率怎么记：4-bit Count-Min Sketch

给每个 key 存一个精确计数器太贵。TinyLFU 的做法是 Count-Min Sketch：

- 一张 `ceiling_pow2(capacity)` 个 long 的表
- 每个 long 装 **16 个 4-bit 计数器**
- 每个 key 用 **4 个哈希**映射到 4 个计数器
- **估计值 = 4 个计数器的最小值**
- 递增时各自 **饱和在 15**

内存账：

```
计数器总数 = 16 × capacity
每个计数器 4 bit
→ 16 × 4 bit = 64 bit = 8 字节 / 条目
```

这正是 wiki 里那句 *"4-bit CountMinSketch, growing at 8 bytes per cache entry"* 的来历。本 demo 对 `capacity=100` 实测 `n_longs=128`、`memory_bytes=1024 = 8 × 128`。

**Count-Min Sketch 只会高估、不会低估**——碰撞只会让某个计数器偏大，而取最小值使得估计值 ≥ 真实次数。这个方向性很重要：高估会让一次性条目**偶尔混入**（本 demo 中 500 个扫描项里有 **5 个**因碰撞混入，495 个被拒），而低估则会误伤热数据，后者代价大得多。

### 2.3 准入判据：候选频率 > 受害者频率

窗口 LRU 的 LRU 端条目（candidate）要和主区的受害者（victim）比频率：

```
if frequency(candidate) > frequency(victim):
    淘汰 victim，candidate 进入主区
else:
    candidate 直接丢弃
```

判据是**严格大于**，平局时 incumbent 胜。这带来一个重要性质：**一次性条目永远进不来**（频率 1 打不过任何已有条目），所以扫描无法污染缓存。

### 2.4 冷启动陷阱：准入测试不能在缓存装满前生效

这是本 demo 实现时踩到的坑，值得单独记：

如果从第一条访问就开始做准入测试，那么冷启动时**所有条目的频率都是 1**，`1 > 1` 为假 → 每一个新条目都被拒 → **缓存永远装不满**（实测稳定在 2 条）。

正确的做法是：**只有在缓存已满（`size >= capacity`）时才做准入测试**；还没装满时，窗口 LRU 的条目直接降级进主区。Caffeine 里对应 `windowWeightedSize > windowMaximum` 且总量已达 `maximum` 的分支。

### 2.5 为什么需要窗口：接住 recency burst

纯 TinyLFU 有个致命问题——**新条目永远进不来**，于是工作负载一旦漂移，缓存就冻住了。

窗口的作用（wiki 原话）：

> The window allows the policy to have a high hit rate when entries exhibit **recency bursts** which would otherwise be rejected

本 demo 的突发测试：主区填满 100 个高频条目（各访问 10 轮），然后访问一个全新条目 `X`，间隔 `gap` 个其它访问后再访问 `X`：

| 窗口比例 | gap=3 | gap=30 |
| --- | --- | --- |
| 0（纯 TinyLFU） | miss | miss |
| 5% | **hit** | miss |
| 20% | **hit** | miss |

只要突发间隔**小于窗口容量**，第二次访问就直接命中——根本不经过准入测试。窗口越大能接住越长的突发，但留给主区的空间就越小，所以窗口大小要自适应。

### 2.6 自适应：hill climbing

wiki：

> The size of the window vs main space is **adaptively determined using a hill climbing optimization**

简化版规则：比较窗口与主区各自的命中率，哪边高就往哪边挪一点。本 demo 实现为单次 ±1%，并夹在 `[1%, 50%]`。

### 2.7 与 ARC / LIRS 的对比

wiki 给出的取舍非常干脆：

| 策略 | 额外内存 |
| --- | --- |
| ARC | 需要 **2 倍**缓存大小来保留被淘汰的 key；且有专利 |
| LIRS | 需要 **3 倍**缓存大小才能达到最高效率 |
| **W-TinyLFU** | **不保留被淘汰的 key**，只多 8 字节/条目的 sketch |

## 三、对比

| 维度 | LRU | LFU | W-TinyLFU |
| --- | --- | --- | --- |
| 记录什么 | 最近一次访问时间 | 访问次数 | 概率化历史频率 |
| 抗扫描污染 | 无 | 有 | 有 |
| 响应负载漂移 | 快 | **极慢**（老热数据赖着不走） | 快（窗口 + sketch 老化） |
| 每条目开销 | 指针（~16 B） | 计数器（~4 B） | sketch 8 B |
| 实现复杂度 | 低 | 中 | 中高 |

## 四、环境

- Python 3.13（纯标准库）
- Go 1.22+（仅 `container/list`、`fmt`）

## 五、运行方式

```bash
cd 07-数据存储/03-缓存/WTinyLFU准入
python wtinylfu_selftest.py     # 30 条断言
go run wtinylfu.go wtinylfu_check.go
```

## 六、关键代码

频率草图的核心只有几行，但方向性（只高估）是整个策略的安全边界：

```python
def increment(self, key):
    self.size += 1
    if self.size >= self.reset_threshold:
        self.reset()
    for i in self._indices(key):
        if self.table[i] < COUNTER_MAX:      # 4-bit，饱和在 15
            self.table[i] += 1

def frequency(self, key):
    return min(self.table[i] for i in self._indices(key))   # 取最小值
```

准入与容量收敛分两阶段，且**准入只在满仓时生效**：

```python
while len(self.window) > self.window_max:
    candidate, _ = self.window.popitem(last=False)
    if self.size < self.cap:
        self.probation[candidate] = True     # 没装满 → 直接进，不做准入
        continue
    victim = self._main_victim()
    if victim is not None and (self.sketch.frequency(candidate)
                               > self.sketch.frequency(victim)):
        self.admitted += 1
        self._pop_main(victim)
        self.probation[candidate] = True
    else:
        self.rejected += 1
```

## 七、性能边界

- sketch 的 `increment` / `frequency` 都是 O(1)，但频率是**概率估计**，不是精确值
- 4-bit 计数器在高频场景下会饱和到 15，之后**所有热条目频率都相同**，区分度归零——这时退化为「平局保 incumbent」，靠 `reset()` 老化恢复区分度
- 本 demo 的 `reset` 阈值（10×capacity）是**本 demo 的设计选择**，Caffeine wiki 未展开具体老化参数
- 窗口越大抗突发越强，但主区变小、整体命中率可能下降——这正是 hill climbing 要权衡的
- 本 demo 的哈希是简化的确定性 64 位混合，未做 Caffeine 那样的 `spread`/`rehash` 分块优化；容量很小时碰撞率会明显高于生产实现

## 八、注意事项与常见坑

1. **准入测试不能在缓存装满前生效**，否则冷启动时所有条目频率都是 1，缓存永远装不满（本 demo 实测卡在 2 条）。
2. **判据是 `>` 不是 `>=`**：平局时新条目进不来，这是「抗扫描」的一部分，别顺手改成 `>=`。
3. **Count-Min 只会高估**：500 个扫描项里仍有 5 个因碰撞混入，这是概率结构的固有代价，不是 bug。
4. **4-bit 会饱和**：访问超过 ~15 次后所有热条目频率相同，必须靠周期性老化恢复区分度。
5. **窗口不是越大越好**：它挤占主区，且窗口本身是纯 LRU，同样会被扫描冲垮。
6. **测量准入计数要在探测访问之前快照**——本 demo 第一次写断言时把探测访问算进去了，导致 `rejected` 从 495 变成 500（是断言写错，不是实现错）。
7. **W-TinyLFU 不保留被淘汰的 key**，这与 ARC/LIRS 有本质区别，也意味着「刚被淘汰的 key 再访问」没有捷径。
8. **本 demo 未复现 Caffeine 的完整实现**（无 `doorkeeper`、无 ReadBuffer/WriteBuffer 的并发优化），只复现可判定的核心语义。

## 九、参考资料

- ben-manes/caffeine Wiki — *Efficiency*（作者本人维护：LRU 在 full scan 下的缺陷、ARC 需 2× / LIRS 需 3× 且 ARC 有专利、W-TinyLFU 的窗口 + Segmented LRU 结构、hill climbing 自适应、4-bit CountMinSketch 与 8 bytes/entry、不保留被淘汰 key） — https://github.com/ben-manes/caffeine/wiki/Efficiency
- Einziger, Friedman, Manes — *TinyLFU: A Highly Efficient Cache Admission Policy*（wiki 中引用的原始论文链接，本轮 arxiv 与 github raw 均不可达，故 wiki 中未展开的参数（如 doorkeeper、老化阈值）本 demo 未做断言） — http://arxiv.org/pdf/1512.00727.pdf
