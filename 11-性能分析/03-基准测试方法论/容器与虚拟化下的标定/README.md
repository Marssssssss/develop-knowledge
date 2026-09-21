# 虚拟化与容器环境下的 `-benchtime` 标定

> 待研究项落地：`虚拟化与容器环境（cgroup CPU quota、steal time）对 -benchtime 标定结果的影响`。结论很反直觉：**限流不会改变标定出的 N，却会把 ns/op 放大约 P/Q 倍**——而"缩短 benchtime"居然能绕开这个偏差。

## 简介

`-benchtime=1s` 承诺的是"**墙钟**至少 1 秒"，不是"**CPU** 至少 1 秒"。在 CPU 配额是 50% 的容器里，这 1 秒墙钟里只有约 0.5 秒真的在跑你的代码，剩下的是被 throttle 的等待。于是：

| 场景 | 标定出的 N | 报出的 ns/op |
| --- | --- | --- |
| 裸机 | 1 000 000 | 1000（真值） |
| cgroup 50% | 1 000 000（**一样**） | 1950（约 2×） |
| cgroup 50% + steal 10% | 1 000 000 | 2166 |
| cgroup 50%，`-benchtime=10ms` | 10 000 | **1000（无偏差）** |

## 原理详解

### 1. cgroup CPU 配额怎么被解析（Go runtime 的做法）

v2 的 `cpu.max` 是 `"<quota> <period>"`，**quota 可以是字面量 `max`**：

```go
quotaStr := buf[:i]
if bytealg.Compare(quotaStr, []byte("max")) == 0 {
    return 0, false, nil        // No limit.
}
periodStr := buf[i+1:]          // 再按 \n 截断
return float64(quota) / float64(period), true, nil
```

v1 走 `cpu.cfs_quota_us`（`-1` 表示不限），解析前**先按换行截断**，没有换行直接算 `errMalformedFile`。两者还有一个优先级细节（`parseCPUCgroup` 的注释原文）：

> cgroup v2 has hierarchy-ID 0. If a v1 hierarchy contains "cpu", that is the CPU controller. Otherwise the v2 hierarchy (if any) is the CPU controller. It is not possible to mount the same controller simultaneously under both the v1 and the v2 hierarchies.

即**命中 v1 的 CPU controller 立刻返回**，v2 只是兜底（自检 E3c）。另外 `containsCPU` 是按逗号切分后的**精确 token** 匹配，所以 `cpuacct` 不算（E4b）。

> `cpu.cfs_period_us` 的默认值因发行版与编排器而异，本 demo **不硬编码**，一律作为参数注入。

### 2. CFS 带宽的量化效应：前 Q 的 CPU 时间是免费的

period 长 `P`、配额 `Q` 的 cgroup，在每个 period 内最多跑 `Q` 的 CPU 时间，配额耗尽后被 throttle 到下个 period。跑完 `R` 的 CPU 工作所需墙钟是

```
k = ceil(R / Q)
wall = (k-1) * P + (R - (k-1) * Q)
```

推论有三个，都可以实测：

- **`R ≤ Q` 时完全不受影响**：`wall(50ms) = 50ms`，吞吐 1.0（E8a/E8b）
- **配额边界处是悬崖**：`wall(51ms) = 101ms` —— 多 1 ms 的活，墙钟跳 51 ms（E7c）
- **足够长时趋近 `Q/P`**：`wall(1s) = 1.95s`（0.513），`wall(1000s)` → 0.5（E8d/E8e）

所以"在受限容器里 ns/op 变差"**不一定是你的代码变慢了**，也可能只是配额——而且**偏差随 benchtime 增大而增大**。

### 3. 为什么 N 不变而 ns/op 变了

`predictN` 只看墙钟：

```go
n := goalns * prevIters / prevns   // 先乘后除
n += n / 5                          // 1.2x
n = min(n, 100 * last)              // 不要增长太快
n = max(n, last + 1)                // 至少比上次多 1
n = min(n, maxBenchPredictIters)    // 1e9
```

标定循环是 `for n := 1; !b.failed && b.duration < d && n < 1e9`，`runN` 内部 `ResetTimer` 把 duration 归零，所以判据用的是**上一轮**的墙钟（E12）。在 50% 限流下，`prevns` 变大确实会压小 `n`，但 `min(n, 100*last)` 与 `max(n, last+1)` 两道钳制把最终收敛点钉在了同一个量级——实测 N 完全相同（E9c），只有 `ns/op = duration / N` 被放大。

这正是危险所在：**N 看起来正常、跑的次数一样多，只有 ns/op 悄悄翻倍**，从输出上完全看不出是被限流了。

### 4. `-benchtime` 缩短可以绕开偏差

因为前 `Q` 的 CPU 时间免费：把 benchtime 从 1s 降到 10ms（同一份代码、`ns_per_op=1000`），总 CPU 时间只有 10ms，没跨过第一个配额，`ns/op` 回到真值 1000（E10）。代价是样本量从 1e6 掉到 1e4。

工程上的取舍：**先在受限容器里用短 benchtime 拿到"干净"的单次耗时，再用它反推长 benchtime 下应该看到的数字**；或者直接把基准跑在不限流的节点上。

### 5. steal time 是另一回事，且会叠加

`Documentation/filesystems/proc.rst` 对 `/proc/stat` 的 cpu 行只给了一句定义：

```
- steal: involuntary wait
```

它是**自开机累计**的量，必须取两次差值；反映的是"vCPU 处于 runnable 但宿主把 CPU 给了别的 guest"。与 cgroup 配额用尽**不是同一个机制**：配额耗尽时进程在 `cgroup` 的 `cpu.stat` 里留下 `nr_throttled` / `throttled_time`，而 steal 只体现在全局 `/proc/stat` 上。

模型上两者也形状不同：

- **cgroup 限流**：有量化（阶梯），短任务免疫
- **steal**：纯乘性 `wall = cpu / (1 - steal)`，**没有量化**，短任务也照吃不误（E11a/E11b）

两者叠加是**相乘/串联**而不是取大者：50% 限流 + 10% steal 给出 2166，而纯限流是 1950（E11c/E11d）。

### 6. GOMAXPROCS：手动设过就不再跟随 cgroup

Go 1.25 起 runtime 会按 cgroup 的 CPU limit 动态调整 GOMAXPROCS（`sysmonUpdateGOMAXPROCS` → `defaultGOMAXPROCS(0)`）。但源码里有两道闸门：

```go
custom := sched.customGOMAXPROCS
if custom {
    unlock(&computeMaxProcsLock)
    return                      // 手动设过 ⇒ 完全不跟随
}
procs := defaultGOMAXPROCS(0)
if procs == curr {
    return                      // 值没变 ⇒ 不动
}
```

所以**在容器里显式设置 `GOMAXPROCS` 会永久关掉这个自适应**（自检 E13b）——对 `RunParallel`（用 `parallelism*GOMAXPROCS` 个 goroutine）这类并行基准，这会直接影响 ns/op。

## 环境依赖

- Python ≥ 3.9（仅标准库）；Go ≥ 1.21（`go run .`）

## 运行方式

```bash
cd 11-性能分析/03-基准测试方法论/容器与虚拟化下的标定
python python/main.py            # 冒烟：三种环境的标定结果 + 吞吐随规模变化
python python/selfcheck_cfs.py   # 45 条断言，全绿
cd go && go run .                # Go 版同模型
```

## 关键代码

| 文件 | 职责 |
| --- | --- |
| `python/main.py` | `parse_v1_number` / `parse_v2_limit` / `parse_cpu_cgroup` / `contains_cpu`；`CfsBandwidth.wall_ns` 的量化闭式；`StealTime`；`predict_n` 四步钳制；`launch` 标定循环；`GomaxprocsController` |
| `python/selfcheck_cfs.py` | 45 条断言：解析边界、v1 优先级、四步钳制、先乘后除、配额悬崖、吞吐趋近、N 不变而 ns/op 翻倍、短 benchtime 免疫、steal 叠加、GOMAXPROCS 闸门 |
| `go/cfsbench.go` | 同模型的 Go 版（230 行） |

## 性能边界与注意事项

- **不要跨环境比较 ns/op**：同一个 commit 在裸机 CI 与受限容器里跑出的 ns/op 系统性不同，差值主要来自 Q/P 而不是代码。
- **看到 ns/op 集体恶化先查配额**，不要先怀疑代码——尤其当 N 没变而 ns/op 变了的时候。
- **`/proc/stat` 的 steal 是自开机累计的**，单次读取没有意义，必须取差值并除以间隔。
- **`cpu.cfs_period_us` 的默认值不要硬编码**（发行版与编排器各异），本 demo 全部以参数注入。
- **`GOMAXPROCS` 一旦手动设置就关掉了容器自适应**，并行基准的并发度会停在手动值上。
- **`predict_n` 的 `100*last` 与 `last+1` 两道钳制**在墙钟抖动（限流 + steal）下会让标定轮数变多，多出来的每一轮都在消耗配额。

## 参考资料（实际阅读过的来源）

- [`golang/go` — `src/internal/runtime/cgroup/cgroup.go`](https://github.com/golang/go/blob/master/src/internal/runtime/cgroup/cgroup.go) — `parseV1Number` 的换行截断、`parseV2Limit` 的 `max` 字面量与 `quota/period`、`parseCPUCgroup` 里 v1 优先于 v2 的注释与实现、`containsCPU` 的 token 匹配、`ErrNoCgroup` / `errMalformedFile` 两类错误
- [`golang/go` — `src/testing/benchmark.go`](https://github.com/golang/go/blob/master/src/testing/benchmark.go) — `predictN` 的四步钳制与"先乘后除"的注释、`prevns == 0` 上取整（go.dev/issue/70709）、`maxBenchPredictIters = 1e9`、`launch` 的循环判据、`runN` 里 `ResetTimer` 归零 duration
- [`golang/go` — `src/runtime/proc.go`](https://github.com/golang/go/blob/master/src/runtime/proc.go) — `sysmonUpdateGOMAXPROCS` 里 `customGOMAXPROCS` 与"值没变就不动"两道闸门、`defaultGOMAXPROCS(0)` 的调用点、`defaultGOMAXPROCSUpdateEnable` 与 `updatemaxprocs=0` 的关闭方式
- [Linux `Documentation/filesystems/proc.rst`](https://github.com/torvalds/linux/blob/master/Documentation/filesystems/proc.rst) — `/proc/stat` cpu 行各字段定义，其中 `steal: involuntary wait`、`iowait` 不可靠的说明
