# pprof top / top -cum / list 三板斧解读

## 简介

- `go tool pprof` 的交互命令 `top` 是剖析的第一入口：它把"每秒 ~100 次的栈采样"聚合每函数两列关键指标——**flat**（该函数自身正在运行）与 **cum**（该函数在调用栈上）。
- 核心价值：`top` 找**热点函数自身**（flat），`top -cum` 找**热点入口/调用链主干**（cum，根帧 flat=0 也能登顶），`list` 下钻到行级；`--nodefraction` 给调用图去噪。
- 关键概念：
  - **flat vs cum**：flat = 采样瞬间该函数是叶子（正在运行，非等待被调函数返回）；cum = 该函数出现在栈上任意位置（自身或其调用的函数在运行）
  - **栈深截断**：每个栈样本只含靠执行侧的 100 帧，深递归会截掉根帧
  - **running%（第三列）**：列表内 flat 占比的累计和
  - **nodefraction**：过滤 cum 占比不足阈值的节点

## 原理详解

依据 Go 官方博客《Profiling Go Programs》（Russ Cox，本轮实读）：

1. **采样机制**：CPU 剖析开启后，Go 程序**每秒约 100 次**停下并记录当前 goroutine 栈上的程序计数器。2525 个样本 ≈ 程序运行了 25 秒多——样本数本身就是耗时估计。
2. **top 五列含义**（博客逐列解释原文口径）：
   - 第 1/2 列：该函数**正在运行**（"as opposed to waiting for a called function to return"）的样本数与占比，即 flat；top 默认按它排序
   - 第 3 列：**列表内累计百分比**（running total），前三行合计 32.4% 即此意
   - 第 4/5 列：该函数**出现在栈上**（"either running or waiting for a called function to return"）的样本数与占比，即 cum
   - 经典实例：`main.FindLoops` flat 10.6%（自身运行）但 cum 84.1%（它或它调用的函数在 84.1% 的样本里运行）
3. **-cum 排序**：按第 4/5 列排序。博客输出里 `gosched0`/`main.main`/`runtime.main` flat 全为 0 却以 cum 84.9% 居首——**找"主干入口"必须用 -cum，默认 top 会把它们排到最后**。
4. **栈深截断（100 帧）**：每个栈样本只含**靠执行侧的 100 帧**。博客实例：`main.main` 的 cum 本应 100%，但递归的 `main.DFS` 在约四分之一的样本里比 `main.main` 深出 100 帧以上，完整栈被截断，于是 main.main 只剩 84.9%。**截掉的是根侧**（否则根帧永远在场、不可能低于 100%），flat 归因（叶子）不受影响。
5. **nodefraction 去噪**：`go tool pprof --nodefraction=0.1` 忽略占比不足 10% 的节点——博客用它在 `web mallocgc` 的调用图里滤掉小节点，让 `FindLoops → mallocgc` 的粗箭头显现。
6. **内存剖析变体**：`--inuse_objects` 报告**分配次数**而非字节数；内存采样是 1-in-524288（约每 512KB 抽 1 块）的近似值。`list FuncName` 下钻到源码行（flat/cum 两列同行显示）。
7. **解读三板斧顺序**：top（谁自身最热）→ top -cum（哪条主干最重）→ list 函数名（热在哪一行）→ web/调用图（配 nodefraction 去噪）。

```
样本(栈,根在前) ──截断(保叶子侧100帧)──┐
                                       ├─ flat[f] = f 是叶子的样本数
                                       ├─ cum[f]  = f 在栈上的样本数
                     top: 按 flat 降序 ─┤
                     top -cum: 按 cum 降序（根帧 flat=0 可登顶）
                     nodefraction: 过滤 cum/total < 阈值
```

## 对比 / 选型

| 命令 | 排序键 | 适合找什么 |
| --- | --- | --- |
| `top` / `topN` | flat 降序 | 自身耗时的热点函数（优化函数体） |
| `top -cum` | cum 降序 | 热点调用链入口（优化算法/减少调用） |
| `list fn` | 行级 flat/cum | 具体哪一行在烧 CPU/分配 |
| `web [fn]` | 调用图 | 结构性认知，配 --nodefraction 去噪 |

## 环境准备

- Python ≥ 3.10 / Go ≥ 1.21（Go 版本机无工具链，走人工审查 + 括号配平）

## 运行方式

```bash
python3 python/pprof_top.py   # 10 组断言
# go run go/pprof_top.go      # 同 10 组断言
```

## 关键代码片段

```python
def aggregate(samples):
    flat, cum = {}, {}
    for stack in samples:
        t = truncate(stack)          # 保叶子侧 100 帧，根侧深递归被截
        flat[t[-1]] = flat.get(t[-1], 0) + 1   # flat：叶子帧（正在运行）
        for f in set(t):
            cum[f] = cum.get(f, 0) + 1         # cum：出现即计
    return flat, cum, len(samples)
```

## 性能与边界

- 采样率约 100 Hz：**分辨率下限 = 10ms**，比这更短的函数很难被 flat 捕捉到；总样本数 ÷ 100 ≈ 采集时长。
- 内存剖析按 1/524288 采样，**大额分配才准**，微小高频分配会被低估（近似值口径来自官方博客）。
- 栈深 100 帧截断：**极深递归会把根帧归因稀释**，看到 main.main cum < 100% 先怀疑截断而不是怀疑调度异常。

## 注意事项与常见坑

- **flat=0 的行不是没用的行**：`top -cum` 下 gosched0/main.main 这类 0 flat 高 cum 的行正是主干入口，被默认 top 排到末尾极易漏看。
- **深递归的截断方向**：被截的是**根侧**（main.main 消失），叶子侧（正在执行的深递归帧）保留——方向想反的话 main.main 的 cum 永远是 100%，与博客 84.9% 的实例矛盾。
- **running% 是"列表内累计"**：行数被 topN 截断时它 ≠ 全局累计，别拿它做 100% 对账。
- **nodefraction 的分母是总样本**：`--nodefraction=0.1` 即"忽略不足总量 10% 的节点"，过滤发生在 cum 维度。
- **先看采样总量**：`Total: N samples` 太小（如 <几百）说明采集窗口过短，结论不可靠。

## 参考资料（实际阅读过的权威来源）

- [Profiling Go Programs - Go Blog（Russ Cox）](https://go.dev/blog/pprof) — top 五列逐列定义、-cum、100 帧截断与 84.9% 实例、nodefraction、--inuse_objects、1-in-524288 内存采样
- [runtime/pprof - Go Packages](https://pkg.go.dev/runtime/pprof) — heap/allocs 快照口径（inuse vs allocs 四种显示）
- [net/http/pprof - Go Packages](https://pkg.go.dev/net/http/pprof) — 采集成因（在线端点如何产出这些 profile 文件）
