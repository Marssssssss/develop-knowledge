# 优雅关闭与连接排空（Graceful Shutdown / Connection Draining）

后端进程「退出」这件事，90% 的线上抖动不是来自代码逻辑，而是来自**退出时序**：监听器什么时候关、在途请求能不能跑完、keep-alive 连接什么时候被掐、负载均衡器什么时候才真的不再把新流量打过来。本 demo 把 Go `net/http` 的 `Server.Shutdown` / `Server.Close` 与 Kubernetes 的 Pod 终止时间线做成可执行模型，逐条对照官方源码与官方文档。

## 一、两个 API 的分野

| | `Shutdown(ctx)` | `Close()` |
| --- | --- | --- |
| 监听器 | 立即关闭 | 立即关闭 |
| idle 连接 | 立即关闭 | 立即关闭 |
| **在途（StateActive）连接** | **保留，等它回到 idle** | **无差别 `rwc.Close()`** |
| 结束条件 | 全部连接 quiescent 才返回 | 立即返回 |
| ctx 到期 | 返回 `ctx.Err()`，连接仍在 | — |
| hijacked（WebSocket） | 不管，需用 `RegisterOnShutdown` 自行通知 | 同样不管 |

源码依据（`src/net/http/server.go`）：`Close` 的循环是

```go
for c := range s.activeConn {
    c.rwc.Close()
    delete(s.activeConn, c)
}
```

而 `Shutdown` 只在 `closeIdleConns` 里关 `StateIdle` 的连接。**这就是两者唯一的实质差别**，也是「滚动发布时出现 502」最常见的根因：用了 `Close`（或没等 `Shutdown` 返回就退出 `main`）。

## 二、Shutdown 的精确流程

```text
inShutdown.Store(true)
  → closeListenersLocked()        // 不再 accept 新连接
  → for _, f := range s.onShutdown { go f() }   // 钩子并发跑，不等待
  → listenerGroup.Wait()          // 等所有 Serve 退出
  → for {
        if closeIdleConns() { return lnerr }    // 注意：进入循环后**先关一次**再等
        select {
        case <-ctx.Done(): return ctx.Err()
        case <-timer.C:    timer.Reset(nextPollInterval())
        }
    }
```

`closeIdleConns` 的判据（实测最容易踩的三条）：

1. `StateNew` 且 `unixSec < now-5s` → **当作 idle 关掉**（Issue 22682：建连后一直不发请求头的慢客户端不能无限拖住关机）。
2. `unixSec == 0` → 视为「刚建好、状态还没落」，**不算 quiescent**，即使状态已经是 `StateIdle`。
3. 只要有任何一条连接不属于上述可关集合，`quiescent` 就是 `false`，继续轮询。

## 三、轮询间隔：1ms 起、翻倍、夹到 500ms

```go
pollIntervalBase := time.Millisecond
nextPollInterval := func() time.Duration {
    interval := pollIntervalBase + time.Duration(rand.IntN(int(pollIntervalBase/10))) // +≤10% 抖动
    pollIntervalBase *= 2
    if pollIntervalBase > shutdownPollIntervalMax { pollIntervalBase = shutdownPollIntervalMax } // 500ms
    return interval
}
```

抖动为 0 时的精确序列（模型实测）：

```text
1, 2, 4, 8, 16, 32, 64, 128, 256, 500, 500, 500, ...
```

即 `closeIdleConns` 的调用时刻是 `0, 1, 3, 7, 15, 31, 63, 127, 255, 755, 1255, ...` ms。**首次调用发生在任何等待之前**，所以「Shutdown 至少要等一个 tick」是错的。

抖动取满时首个间隔是 `1.1ms`（+10%），取 0 时是 `1.0ms`。抖动的意义是避免大量实例在同一毫秒集体轮询。

## 四、两个反直觉的语义

**1. `SetKeepAlivesEnabled(true)` 救不了一个正在关机的 server。**

```go
func (s *Server) doKeepAlives() bool {
    return !s.disableKeepAlives.Load() && !s.shuttingDown()
}
```

`shuttingDown()`（即 `inShutdown`）是**独立的第二道闸**。Shutdown 一旦开始，server 不再对外宣告 keep-alive，正在被复用的连接会在当前请求结束后自然终止，而不是继续复用。想在关机后恢复 keep-alive 做不到——`inShutdown` 没有反向的设置器，只能新建 Server。

**2. `trackListener` 在关机会返回 `false`。**

`Serve` 靠这个返回值判定「server 已经关了」，从而返回 `ErrServerClosed`。这也是为什么 `Shutdown` 之后**不能复用同一个 Server 实例**再 `Serve`。

## 五、Kubernetes 侧的协同

`kubelet` 的 Pod 终止时序（默认 `terminationGracePeriodSeconds = 30`）：

```text
0s   Pod 标记 Terminating；EndpointSlice 中的 endpoint 置 ready=false（但**不立即摘除**）
     ↓
     preStop 钩子（若配置）—— 钩子跑超时，kubelet 只给**一次性的 +2 秒**宽限延期
     ↓
     TERM 送达容器内的 1 号进程
     ↓
     应用排空在途请求
     ↓
宽限到期 → 仍在运行的容器收 SIGKILL
```

关键实践点：

- **endpoint 置 `ready=false` 与 kube-proxy/Ingress 实际生效之间存在时间差**，所以收到 TERM 后应当再「装死」几秒（常见写法是 `preStop: sleep 5`）再开始关监听，避免这段时间里还在接新流量。
- **`preStop` 的耗时算在宽限期里**（源码/文档口径：钩子超期后只给一次性 2 秒延期，不是每次都给）。所以 `preStop: sleep 10` + `terminationGracePeriodSeconds: 30` 意味着留给应用排空的时间只剩 20 秒。
- 应用的排空时间必须 **< 宽限期 − preStop 耗时**，否则被 SIGKILL，在途请求直接断。

## 六、运行方式

```bash
python selfcheck_shutdown.py     # 34 项断言
```

## 七、关键代码

- `main.py` — `Server.shutdown()` / `Server.close()` / `Server.close_idle_conns()` / `pod_termination()`
- `shutdown.go` — 同构的 Go 实现，`inShutdown` 用 `atomic.Bool` 表达，便于看 `DoKeepAlives()` 的真实依赖
- `selfcheck_shutdown.py` — 对照实验：同一夹具分别喂 `Shutdown` 与 `Close`，断言差异集

## 八、性能与边界

| 项 | 值 |
| --- | --- |
| 轮询间隔 | 1ms 起，翻倍，上限 500ms（`shutdownPollIntervalMax`） |
| 抖动 | `rand.IntN(base/10)`，即 [0, +10%) |
| StateNew 视为 idle 的阈值 | 5 秒（Issue 22682） |
| 默认 Pod 宽限 | 30 秒 |
| preStop 超期延期 | 一次性 +2 秒 |
| 无法被 Shutdown 关闭的 | hijacked 连接（WebSocket 等）、h3Server 需单独处理 |

## 九、注意事项与常见坑

1. **`Shutdown` 返回前不能让 `main` 退出**。`ListenAndServe` 会立刻返回 `ErrServerClosed`，此时若 `main` 结束，进程带着在途请求一起没了。
2. **ctx 到期不等于连接被关**。`Shutdown` 返回 `context.DeadlineExceeded` 后，`activeConn` 里那些顽固连接还在——要么再调 `Close()` 强制收尾，要么接受它们被运行时回收。
3. **WebSocket / SSE 要自己管**。文档明确写了 `Shutdown` 不尝试关闭也不等待 hijacked 连接，正确做法是 `RegisterOnShutdown` 里发关闭帧并自行等待。
4. **`Close()` 不是 `Shutdown()` 的降级版**，它是硬关。滚动发布里把它当成兜底可以，但不能当主路径。
5. **宽限期是全局的**，包含 preStop + 应用排空 + 容器运行时开销，别只按应用排空时间配。
6. `RegisterOnShutdown` 的钩子是 `go f()`，并发且**不等它完成**；钩子内部要自己做同步。

## 十、参考资料

- golang/go `src/net/http/server.go` — <https://raw.githubusercontent.com/golang/go/master/src/net/http/server.go>（`Shutdown` / `Close` / `closeIdleConns` / `shutdownPollIntervalMax` / `trackListener` / `doKeepAlives` / `RegisterOnShutdown`）
- Kubernetes — Pod Lifecycle / Pod termination — <https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/>
- Go `net/http.Server` 文档 — <https://pkg.go.dev/net/http#Server.Shutdown>
