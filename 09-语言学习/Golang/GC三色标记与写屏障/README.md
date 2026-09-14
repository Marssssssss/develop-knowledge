# GC 三色标记与写屏障

## 简介

- Go 的垃圾回收器是**并发三色标记-清扫** GC(非移动):标记阶段与应用并发执行,靠**混合写屏障**(Go 1.8)维持弱三色不变式,从而消除了 1.5~1.7 时代最痛的 STW 栈重扫。
- 关键概念:
  - **三色抽象**:白(未访问)/ 灰(已访问、子引用未扫完)/ 黑(完全扫完);并发标记期间应用还在改指针,必须靠屏障防"丢对象"
  - **混合写屏障**:`shade(*slot)` 无条件(Yuasa 删除侧)+ 当前栈灰时 `shade(ptr)`(Dijkstra 插入侧)
  - **弱三色不变式**:黑对象可以指白对象,但该白对象必须被某灰对象经白色指针链保护(grey-protected)
  - **GOGC**:目标堆公式,决定 GC 频率与 CPU/内存权衡
  - **Pacer**:反馈循环决定何时启动 GC,高分配压力时让 mutator 做标记援助(mark assist)
- 历史背景:2014 年 Go 因 GC 延迟面临生存危机(ISMM 2018 演讲:"If Go does not solve this GC latency problem somehow then Go isn't going to be successful");1.5 上并发 GC,1.8 上混合写屏障。

## 原理详解

### 延迟演进(全部为 Twitter 生产环境实测,来自 ISMM 2018 演讲)

| 版本 | STW 暂停 | 关键改动 |
| --- | --- | --- |
| Go 1.4 | 300~400 ms | STW 标记 |
| Go 1.5 | **→ 30~40 ms** | 并发三色标记上线(Dijkstra 插入屏障) |
| Go 1.6 | → 4~5 ms | 消除 STW 期间 O(heap) 操作 |
| Go 1.8 | **→ sub-millisecond** | **混合写屏障**,消除周期末 STW 栈重扫 |
| 2018 目标 | 500 µs / 轮 | 最小化稳态 mark assist |

### 为什么 1.7 需要栈重扫(proposal 17503)

1. 1.7 的 Dijkstra 插入屏障只拦**堆写**;给栈写加屏障代价不可接受,于是栈只能保持 **permagrey**(永久灰)。
2. GC 周期开始扫栈收集根;但栈一旦再次执行,无法保证不含白色引用 → **已扫的栈保守退回灰色**。
3. 周期末必须 STW 重扫所有灰栈,最坏 **10s~100s ms**(大量活跃 goroutine 的应用)。

### 混合写屏障(Go 1.8,proposal 17503 原文伪代码)

```text
writePointer(slot, ptr):
    shade(*slot)                 # Yuasa 删除侧:无条件着色被覆盖的旧值
    if current stack is grey:    # 本 goroutine 栈尚未扫描时
        shade(ptr)               # Dijkstra 插入侧:着色新写入的指针
    *slot = ptr
```

两处 shade 的分工(原文论证):

- `shade(*slot)` 阻止 mutator"把唯一指针从堆挪进栈"——从堆 unlink 时对象被着色;
- `shade(ptr)` 阻止"把栈里唯一的白指针装进黑堆对象"——装入时被着色;
- 栈变黑后 `shade(ptr)` 不再必要(栈只指已着色对象,且删除侧兜底)。

效果:**栈一旦扫描变黑就永久保持黑**,重扫、rescan list、栈屏障全部删除;代价是更多浮动垃圾(标记阶段任一时刻从根可达的对象都会活过本轮)。前置要求:对象**分配即为黑**。不变式从强三色降为**弱三色**(Pirinen '98):正确性由"sound + bounded + complete"形式化证明 + 随机化模型检验支撑。

### GOGC 与 Pacer(gc-guide)

```text
目标堆 = 存活堆 + (存活堆 + GC 根) × GOGC/100      # Go 1.18+ 计入根
       # 1.18 前:目标堆 = 存活堆 × (1 + GOGC/100)
```

- 官方示例:live 8 MiB + 根 2 MiB("work"=10 MiB):GOGC=100 → 新分配 10 MiB、总足迹 18 MiB;GOGC=50 → 5 MiB;GOGC=200 → 20 MiB。
- 核心结论:**GOGC 翻倍 → 堆开销翻倍、GC CPU 成本约减半**,反之亦然;最小堆 4 MiB 兜底。
- Pacer(ISMM 2018):反馈循环决定启动时机,"标记恰好在内存耗尽前结束";分配速率跑赢标记时,**分配大户 goroutine 被拉去帮忙标记**(mark assist,gcAssistAlloc 累计 >5% 即提示应用分配跑赢 GC)。
- `GOGC=off` / `SetGCPercent(-1)` 关闭 GC;`GOMEMLIMIT`/`SetMemoryLimit`(Go 1.19)是**软限制**(限制 runtime 总内存),防颠簸:GC CPU 上限约 50%(2×GOMAXPROCS CPU 秒窗口),最坏减速 2×;`GOGC=off + GOMEMLIMIT` 组合 = 最小 GC 频率维持内存上限的资源经济性最大化。
- 为什么不分代:"年轻对象在**栈**上生生死死"(逃逸分析 + 值语义),分代 GC 需要常开写屏障,与"只在 GC 期间开屏障"的设计哲学冲突(ROC/分代方案实测编译减速 30-50% 被放弃)。

### GC 周期与 STW 位置

gc-guide 用三态循环描述:**sweeping → off → marking**。两处 STW:标记/清扫相位切换的短暂停顿 + 标记开始挂起 goroutine 扫根;暂停时长与堆大小解耦("Go GC is not fully stop-the-world"),标记期 GC 占 25% CPU。

## 对比 / 选型

| 屏障方案 | 标记开始 | 周期末 | 代价 |
| --- | --- | --- | --- |
| Dijkstra 插入(Go 1.5-1.7) | 立即并发标记 | **STW 重扫全部灰栈** | permagrey 栈 |
| Yuasa 纯删除 | 需先快照/扫栈 | 无重扫 | 快照点后死亡对象成浮动垃圾 |
| **混合(Go 1.8+)** | **并发扫栈** | **无重扫** | 浮动垃圾(实践中与 1.7 相当) |

## 环境准备

- 操作系统:任意
- 语言版本:Go 1.21+ / Python 3.10+
- 依赖:无

## 运行方式

### Go(观测与调优 API)

```bash
cd go
go run gc_tuning.go
```

### Python(三色标记 + 混合写屏障模拟,确定性断言)

```bash
python3 python/tricolor_gc.py
```

## 关键代码片段

混合屏障(对应原理伪代码逐行):

```python
def heap_write(self, s, obj, slot, ptr):
    self.heap.shade(self.heap.objs[obj]["slots"].get(slot))  # shade(*slot) 无条件
    if not s["black"]:          # 当前栈还是灰(未扫描)
        self.heap.shade(ptr)    # shade(ptr)
    self.heap.objs[obj]["slots"][slot] = ptr
```

GOGC 目标堆公式(对应 gc-guide 官方示例):

```go
target := liveHeap + (liveHeap+gcRoots)*uint64(gogc)/100  // 1.18+ 口径
if target < 4<<20 { return 4 << 20 }                      // 最小堆 4 MiB 兜底
```

## 性能与边界

- 1.8 混合屏障初实验:最坏 STW **< 50µs**(对比重扫的 10s~100s ms,约 3 个数量级);屏障本身常量成本约 4-5%(ISMM 2018)。
- 标记期 GC 占 **25% CPU**;mark assist 累计 >5% 说明应用分配跑赢 GC。
- Go GC 非移动:内存碎片靠 size-class 分配器吸收,不做整理。

## 注意事项与常见坑

- **坑 1:以为 `runtime.GC()` 会跑 finalizer**。runtime 文档:只阻塞到 GC 完成;gc-guide:它只**排队** cleanups/finalizers、不等待执行。finalizer 链需要"链深"次 runtime.GC() 才全跑完。
- **坑 2:`SetGCPercent(-1)` 后内存只涨不降**。GOGC=off 等价无穷大,触发条件只剩内存限制。规避:off 必须搭配 GOMEMLIMIT(共享内存环境勿用此组合)。
- **坑 3:把 GOMEMLIMIT 当 OOM 保险**。软限制在接近环境上限时把 OOM 风险换成"严重减速"风险(gc-guide 明确列为 Don't)。
- **坑 4:读屏障从未采用**。Go 长期回避读屏障(开销不确定),ROC/分代因屏障常开被否——设计空间刻意偏向"加内存换低延迟"。
- **坑 5:堆翻倍 ≠ 浪费**。写屏障成本恒定,堆翻倍可把标记成本压到屏障成本之下(ISMM 2018:"doubling memory is going to be a better value than doubling cores")。

## 参考资料(实际阅读过的权威来源)

- [Eliminate STW stack re-scanning(proposal 17503)— github.com/golang/proposal](https://github.com/golang/proposal/blob/master/design/17503-eliminate-rescan.md) — 1.7 重扫问题、混合屏障完整伪代码与三点论证、弱三色不变式、浮动代价、<50µs 数据。
- [Getting to Go: The Journey of Go's Garbage Collector — go.dev/blog/ismmkeynote](https://go.dev/blog/ismmkeynote) — 各版本延迟演进数字、Pacer/assist、不分代理由、写屏障仅在 GC 期开启。
- [A Guide to the Go Garbage Collector — go.dev/doc/gc-guide](https://go.dev/doc/gc-guide) — GOGC 公式与官方示例、GOMEMLIMIT 软限制与 50% CPU 上限、GC 周期三态、runtime.GC() 语义、mark assist 判读。
- [runtime — pkg.go.dev/runtime](https://pkg.go.dev/runtime) — ReadMemStats/GC 的官方语义。
