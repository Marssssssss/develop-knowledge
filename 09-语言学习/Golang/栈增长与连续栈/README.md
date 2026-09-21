# Go 栈增长与连续栈（runtime/stack.go + proc.go 的 newstack）

## 简介

goroutine 的栈从 **2 KB** 起步，不够就整块复制到一块 2 倍大的新内存——这是 Go 1.3 起的
**连续栈（contiguous stacks）** 方案，替代了早期会「栈抖动」的分段栈（split stacks）。
代价是增长时要搬运并修正栈上指针，收益是**没有热翻转（hot split）问题**、且栈就是一块普通内存。

关键概念：

| 概念 | 一句话 |
| --- | --- |
| `stackguard0` | 栈溢出检测的阈值；`sp < stackguard0` 就调 `morestack` |
| `stackMin` / `fixedStack` | 最小栈 2048；`fixedStack` = `stackMin + stackSystem` 上取整到 2 的幂 |
| `stackGuard` | 哨兵区大小 = `stackNosplit + stackSystem + abi.StackSmall` |
| `newstack` | 计算新栈大小：翻倍，不够再翻倍 |
| `copystack` | 整块复制 + 逐帧修正指针（`adjustframe`/`adjustpointers`） |
| `shrinkstack` | GC 时的收缩：用了不到 **1/4** 才缩一半 |

## 原理详解

### 1. 三个栈区常量（互相之间是加法关系）

```go
stackMin     = 2048                                     // runtime/stack.go
stackSystem  = IsWindows*4096 + IsPlan9*512 + IsIos*1024
stackNosplit = abi.StackNosplitBase * sys.StackGuardMultiplier   // 800 × 1
stackGuard   = stackNosplit + stackSystem + abi.StackSmall       // 800 + 0 + 128
fixedStack   = 2 的幂，不小于 stackMin + stackSystem
```

实测三个平台（自检 E1/E2）：

| GOOS | stackSystem | fixedStack | stackGuard |
| --- | --- | --- | --- |
| linux | 0 | 2048 | 928 |
| windows | 4096 | 8192 | 5024 |
| plan9 | 512 | 4096 | 1440 |

> `fixedStack` 用的是官方注释里那串 `fixedStack0..fixedStack6` 位运算，本质是
> **向上取整到 2 的幂**；windows 下 `2048+4096=6144` → `8192`。

`StackGuardMultiplier = 1 + IsAix + IsOpenbsd + isRace`，所以 race 构建下哨兵区翻倍。

### 2. 栈溢出检测：不是「比 SP」，而是「比 3 个哨兵值」

`g.stackguard0` 除了真实阈值，还会被写成三个**比任何真实 SP 都大**的哨兵：

```go
stackPreempt   = uintptrMask & -1314   // 0xfffffade：请求抢占
stackFork      = uintptrMask & -1234   // 0xfffffb2e：正在 fork
stackForceMove = uintptrMask & -275    // 0xfffffeed：调试用强制搬栈
```

`newstack` 一进来先看 `stackguard0 == stackPreempt`：是就**不是栈增长**，而是抢占
（`gopreempt_m` / `preemptPark`），**不会 copystack**。这个分支在 `newstack` 里非常靠前。

### 3. 新栈大小：翻倍，但「至少够放得下当前帧」

```go
oldsize := gp.stack.hi - gp.stack.lo
newsize := oldsize * 2
if f := findfunc(gp.sched.pc); f.valid() {
    max := uintptr(funcMaxSPDelta(f))
    needed := max + stackGuard
    used := gp.stack.hi - gp.sched.sp
    for newsize-used < needed {
        newsize *= 2
    }
}
if newsize > maxstacksize || newsize > maxstackceiling {
    throw("stack overflow")
}
```

实测（linux，guard=928）：`oldsize=2048, used=2000, funcMaxSPDelta=3000` →
`needed=3928`，`4096-2000=2096 < 3928` → **再翻一次到 8192**。
所以「栈一定翻倍」是错的——**可能一次翻两倍甚至更多**。

注意 `newsize-used < needed` 是**严格小于**：恰好相等时不翻倍（自检 E4 边界用例）。

### 4. 上限

```go
if goarch.PtrSize == 8 { maxstacksize = 1000000000 }  // 1 GB
else                   { maxstacksize = 250000000  }  // 250 MB
maxstackceiling = 2 * maxstacksize                    // SetMaxStack 的上限
```

用十进制而不是 `1<<30`，官方注释写得很直白：「因为报错信息里看起来更好看」。
实测：256 MB → 512 MB 正常；512 MB 再翻是 1 073 741 824 > 1 000 000 000 → `stack overflow`。

### 5. 收缩：只在 GC 时，且只用不到 1/4

```go
newsize := oldsize / 2
if newsize < fixedStack { return }
avail := gp.stack.hi - gp.stack.lo
if used := gp.stack.hi - gp.sched.sp + stackNosplit; used >= avail/4 {
    return          // 用了 1/4 以上就不收缩
}
copystack(gp, newsize)
```

三个要点：

1. **`used` 里额外加了 `stackNosplit`**——哨兵区也算「在用」，不能算成空闲；
2. 判据是 `>=`，**恰好等于 1/4 也不收缩**（实测 8192 的栈：`used=2047` 收缩，`used=2048` 不收缩）；
3. 下限是 `fixedStack` 而不是 `stackMin`：windows 下 8192 的栈**永远不会收缩**。

### 6. 什么时候不能收缩

`isShrinkStackSafe` 四条否决：

- `gp.syscallsp != 0`——系统调用期间可能有指向栈的指针，且最内层帧没有精确指针图；
- `gp.asyncSafePoint`——异步安全点同样没有精确指针图；
- `gp.parkingOnChan`——`gopark` 到 `activeStackChans` 置位之间的窗口；
- 状态是 `_Gwaiting` 且 `waitreason.isWaitingForSuspendG()`。

### 7. 连续栈 vs 分段栈

| 维度 | 连续栈（Go 1.3+） | 分段栈（Go 1.2 及之前） |
| --- | --- | --- |
| 结构 | 一块连续内存 | 链表串起来的栈段 |
| 分配 | 复制整块（O(已用)） | 追加一段（O(1)） |
| 热翻转 | 无 | 有：在边界反复调用会反复申请/释放段 |
| 指针修正 | 需要遍历栈帧调整 | 不需要（老段地址不变） |

## 环境准备

- 操作系统：任意；Go 1.21+；Python 3.8+

## 运行方式

```bash
cd python && python selfcheck_stack.py   # 64 条断言
cd python && python main.py              # 五组结论
cd go && go run .                        # 九组结论，内置 check 断言
```

## 关键代码片段

`go/stack.go` 里 `newStack` 的核心（与 proc.go 逐行对应）：

```go
oldSize := g.size()
newSize := oldSize * 2
if hasDelta {
    needed := funcMaxSPDelta + stackGuard(g.Goos, g.Goarch)
    used := g.used()
    for newSize-used < needed {
        newSize *= 2
    }
}
if newSize > maxSize || newSize > 2*maxSize {
    return 0, &StackOverflow{newSize, maxSize}
}
g.copyStack(newSize)
```

## 性能与边界

- 起始 2 KB，64 位上限 1 GB（`SetMaxStack` 最多 2 GB，超过也没用，因为 `maxstackceiling` 会拦）
- **栈增长不是无限便宜的**：每次增长都要 `copystack`，深度递归的每段都会触发
- `morestack` 本身必须在**不分裂的栈空间**里跑完：amd64 上 `morestack` 帧 40 字节、
  `deferproc` 帧 56 字节，都要塞进 `StackGuard - StackSmall` 的底部区域（官方注释）

## 注意事项与常见坑

1. **「栈每次翻倍」是近似说法**：大帧会一次翻多倍，见 §3。
2. **收缩不是即时的**：只在 GC 扫描到该 goroutine 时发生，而且 `debug.gcshrinkstackoff > 0` 会整个关掉。
3. **`used` 含 `stackNosplit`**：手算时漏掉这 800 字节，会把「该收缩」算成「不该收缩」。
4. **windows/plan9 的最小栈更大**（`stackSystem` 不为 0），收缩下限随之上移。
5. `stackguard0 == stackPreempt` 走的是**抢占分支**而不是增长分支——看 `newstack` 源码时容易把两个用途混在一起。
6. 建模时最容易写错的一处：`copystack` 后 **sp 相对栈顶的偏移不变、相对栈底的偏移会变**
   （因为新栈更大）。用「相对栈底偏移守恒」做断言会失败。
7. 无限递归不会 OOM 而是 `fatal error: stack overflow`，因为它先撞到 `maxstacksize`。

## 参考资料（实际阅读过的权威来源）

- [Go 源码 src/runtime/stack.go](https://github.com/golang/go/blob/master/src/runtime/stack.go) — `stackMin/stackSystem/fixedStack*/stackGuard/stackNosplit`、`stackPreempt/stackFork/stackForceMove` 哨兵、`copystack`、`shrinkstack`、`isShrinkStackSafe`、morestack 帧大小注释
- [Go 源码 src/runtime/proc.go](https://github.com/golang/go/blob/master/src/runtime/proc.go) — `newstack` 的新栈大小计算与抢占分支、`maxstacksize/maxstackceiling` 的十进制取值与注释
- [Go 源码 src/internal/abi/stack.go](https://github.com/golang/go/blob/master/src/internal/abi/stack.go) — `StackNosplitBase=800`、`StackSmall=128`、`StackBig=4096`
- [Go 源码 src/cmd/internal/objabi/stack.go](https://github.com/golang/go/blob/master/src/cmd/internal/objabi/stack.go) — `StackGuardMultiplier` 的 `1 + AIX + OpenBSD + race` 算式
- [Go 1.3 Release Notes](https://go.dev/doc/go1.3) — 连续栈替代分段栈的官方说明与热翻转问题的动机
