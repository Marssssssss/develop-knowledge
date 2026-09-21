# Go 网络轮询器 netpoll（runtime/netpoll.go）

## 简介

Go 能在**一个 OS 线程上跑几万个阻塞式读写的 goroutine**，靠的就是 netpoll：
`conn.Read()` 在 fd 没数据时不是阻塞线程，而是把当前 goroutine **park** 在一个
`pollDesc` 的信号量上，线程回去跑别的 goroutine；epoll 说 fd 就绪了，再把那个 goroutine
放回运行队列。

关键概念：

| 概念 | 一句话 |
| --- | --- |
| `pollDesc` | 每个被轮询的 fd 一个，内含 rg/wg 两个**二值信号量** |
| `pdNil/pdReady/pdWait/G` | 信号量的四种取值（0/1/2/`*g` 指针） |
| `netpollblock` | 等 IO：能消费通知就返回，否则 park |
| `netpollunblock` | 唤醒：`ioready` 决定是否置 `pdReady` |
| `netpollWaiters` | 全局「有多少 goroutine 在等 IO」，调度器据此决定能否真的睡下去 |
| `netpollBreak` | 用 eventfd + `netpollWakeSig` CAS 打断 epollwait，**带去重** |

## 原理详解

### 1. 两个二值信号量

官方注释把状态机写得很清楚（netpoll.go 的 `pollDesc` 上方）：

```
pdReady  — 有 IO 就绪通知挂着；goroutine 把它改成 pdNil 来「消费」
pdWait   — goroutine 准备 park 但还没 park：
           要么 commit 成 G 指针，
           要么被并发的 IO 通知改成 pdReady，
           要么被 timeout/close 改成 pdNil
G 指针   — goroutine 已阻塞在此；IO 通知 / timeout-close 把它改成 pdReady / pdNil
pdNil    — 以上都不是
```

`rg` 管读、`wg` 管写，**同一方向同一时刻只允许一个 goroutine 等待**（否则 `throw("runtime: double wait")`）。

### 2. netpollblock：三步 CAS

```go
for {
    if gpp.CompareAndSwap(pdReady, pdNil) { return true }   // ① 消费已挂起的通知
    if gpp.CompareAndSwap(pdNil, pdWait)  { break }         // ② 声明「我要 park」
    if v := gpp.Load(); v != pdReady && v != pdNil {
        throw("runtime: double wait")                       // ③ 既非就绪也非空 → 有人在等
    }
}
if waitio || netpollcheckerr(pd, mode) == pollNoError {
    gopark(netpollblockcommit, ...)                          // commit：CAS(pdWait → gp)
}
old := gpp.Swap(pdNil)
return old == pdReady
```

三个容易读错的细节：

1. **`gopark` 之后的代码只在被唤醒后才执行**。所以「信号量仍是 G 指针」意味着
   **还没人唤醒**，它依旧是阻塞态（本 demo 的模型在这里返回 `false` 而不是抛异常）。
2. 唤醒方分两种：`ioready=true`（真 IO）把信号量置 `pdReady`；`ioready=false`
   （超时/关闭）置 `pdNil`。所以**被超时唤醒后 `netpollblock` 返回 false**，
   上层 `poll_runtime_pollWait` 会去 `netpollcheckerr` 拿到 `ErrTimeout`。
3. `netpollWaiters` 的加减不对称：commit 时 `+1`，`netpollunblock` 发现换出的是
   **G 指针**才 `-1`；如果换出时还是 `pdWait`（对方还没 commit 完），计数**不动**。

### 3. netpollunblock 的 delta 规则

```go
if gpp.CompareAndSwap(old, new) {
    if old == pdWait {
        old = pdNil          // 还没真 park，waiters 还没加过 → 不减
    } else if old != pdNil {
        *delta -= 1          // 换出的是 G 指针 → 减
    }
    return (*g)(unsafe.Pointer(old))
}
```

另外两个提前返回：

- `old == pdReady` → 已经有更「新」的通知了，直接返回 nil（不重复唤醒）；
- `old == pdNil && !ioready` → **超时/关闭不会凭空造出一个 `pdReady`**。

### 4. deadline 靠 seq 失效

`poll_runtime_pollSetDeadline` 里每次改 deadline 都 `pd.rseq++`（写方向 `wseq++`），
timer 的回调 `netpolldeadlineimpl` 第一件事就是比对 seq：

```go
if seq != currentSeq {
    return   // 描述符被复用，或 timer 已被重置 → 丢弃这次到点
}
```

否则 `pd.rd = -1` 且置 `expiredRead`，之后 `netpollcheckerr` 就返回 `ErrTimeout`。

两个边界：

- `d > 0` 时先 `d += nanotime()`；**int64 回绕成负数**就取 `1<<63 - 1`（实测易踩：Python 没有回绕，模型里必须显式模拟）。
- 读写 deadline 相等（combo）时只起**一个** timer（`netpollDeadline`），不等则各起一个。

### 5. 与调度器的衔接

`netpollWaiters > 0` 说明「有 goroutine 在等 IO」，此时 netpoll 不能无限期睡；
timer 的 `wakeTime` 决定最多睡多久。`netpollBreak` 用 `netpollWakeSig.CompareAndSwap(0, 1)`
做去重——**在 eventfd 被读走（WakeSig 归零）之前，重复的 break 会被直接丢弃**。

## 对比 / 选型

| 维度 | Go netpoll（rg/wg 信号量） | 传统 Reactor 回调 | 线程每连接 |
| --- | --- | --- | --- |
| 编程模型 | 同步阻塞式（goroutine 内看起来是阻塞的） | 回调/状态机 | 同步阻塞式 |
| 上下文 | goroutine 栈，随时可增长 | 手写状态结构 | 线程栈（MB 级，固定） |
| 同 fd 并发 | 每方向只允许一个等待者 | 自己管 | 天然 |
| 唤醒粒度 | 单个 goroutine | 回调链 | 线程 |

## 环境准备

- 操作系统：Linux/macOS/Windows 均可（本 demo 是纯状态模型，不真起 epoll）
- Go：1.21+（仅用 `fmt`）
- Python：3.8+

## 运行方式

### Python

```bash
cd python
python selfcheck_netpoll.py   # 72 条断言
python main.py                # 四组可观察结论
```

### Go

```bash
cd go
go run .                      # 七组结论，内置 check 断言
```

## 关键代码片段

`python/netpollmodel.py` 的 `netpollunblock`（与官方逐行对应）：

```python
def netpollunblock(pd, mode, ioready, delta):
    gpp = pd.gpp(mode)
    old = getattr(pd, gpp)
    if old == PD_READY:
        return None, delta          # 已有更新通知，不重复唤醒
    if old == PD_NIL and not ioready:
        return None, delta          # 超时/关闭不造 pdReady
    setattr(pd, gpp, PD_READY if ioready else PD_NIL)
    if old == PD_WAIT:
        old = PD_NIL                # 还没 commit，waiters 不减
    elif old != PD_NIL:
        delta -= 1                  # 换出的是 G 指针
    return (old if old > PD_WAIT else None), delta
```

## 性能与边界

- 「正在 poll 的 fd」集合由 epoll/kqueue/IOCP 维护，本 demo 不建模平台层
- `netpollWaiters` 是全局（不是 per-P）原子计数
- 同一方向二次 `netpollblock` 直接 `throw("runtime: double wait")`——**不要在多 goroutine 里对同一个 fd 同时 Read**
- deadline 溢出取 `1<<63 - 1`（约 292 年），实际等价于「永不过期」

## 注意事项与常见坑

1. **被超时唤醒 ≠ IO 就绪**：信号量是 `pdNil` 不是 `pdReady`，`netpollblock` 返回 false，
   上层必须再查一次错误码。把「被唤醒」当成「可读」就会读到超时错误。
2. **`Stop` 之后 channel 里可能还有值**（time 侧），本模型不涉及；但同类问题是
   `pollDesc` 被复用时旧通知靠 `fdseq`/`rseq` 失效——所以**不要缓存 `pollDesc` 指针**。
3. **`netpollBreak` 有去重**：WakeSig 未归零期间的 break 会被丢掉，这不是丢事件，
   而是「已经有一个 wake 在路上」。
4. `poll_runtime_pollReset` **先查错再清信号量**：closing 状态下它返回 `ErrClosing`
   且**不重置** `rg`。
5. Python 建模时最容易写错的一处：`gopark` 之后的代码不是立刻执行的。若把它当成
   顺序执行，会误判「信号量还是 G 指针」为数据损坏（官方是 `throw("corrupted polldesc")`）。
6. int64 回绕：Python 无溢出，deadline 的溢出分支必须手动折回有符号区间才对得上官方行为。

## 参考资料（实际阅读过的权威来源）

- [Go 源码 src/runtime/netpoll.go](https://github.com/golang/go/blob/master/src/runtime/netpoll.go) — `pollDesc` 的 rg/wg 状态机注释、`netpollblock/unblock/ready`、`poll_runtime_pollReset/Wait/SetDeadline/Unblock`、`netpolldeadlineimpl`、`netpollAdjustWaiters`
- [Go 源码 src/runtime/netpoll_epoll.go](https://github.com/golang/go/blob/master/src/runtime/netpoll_epoll.go) — `netpollBreak` 的 `netpollWakeSig.CompareAndSwap(0, 1)` 去重、eventfd 与 `netpoll()` 主循环
- [Go 源码 src/internal/poll/fd_poll_runtime.go](https://github.com/golang/go/blob/master/src/internal/poll/fd_poll_runtime.go) — `internal/poll` 到 runtime 的 go:linkname 边界
- [Go 官方包文档 net](https://pkg.go.dev/net) — `SetDeadline`/`SetReadDeadline` 的语义
