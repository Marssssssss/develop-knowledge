# HTTP Agent 连接池与 keep-alive 连接复用

## 简介

- `http.Agent` 是 Node.js HTTP 客户端的**连接池管理者**。官方定位："An `Agent` is responsible for managing connection persistence and reuse for HTTP clients. It maintains a queue of pending requests for a given host and port, reusing a single socket connection for each until the queue is empty, at which time the socket is either destroyed or put into a pool where it is kept to be used again for requests to the same host and port."
- 关键默认值（v22.22.2 实测 + 官方文档）：`new Agent()` 的 `keepAlive` **选项**默认 `false`；但 **`http.globalAgent` 自 Node 19 起 `keepAlive === true`**（本机 v22.22.2 实测）；`maxSockets: Infinity`（per origin）；`maxFreeSockets: 256`；`scheduling: 'lifo'`（v15.6.0 起从 fifo 改为 lifo）；`keepAliveMsecs: 1000`。
- 复用判定键：`agent.getName()` 返回 `host:port:localAddress[:family]`——同 origin 的空闲 socket 才会被复用。

## 原理详解

### keep-alive 复用与请求排队（E1/E3）

- HTTP/1.1 keep-alive 连接上，**响应按连接内的顺序与请求配对**（官方："responses are associated with requests by their order on that connection. HTTP/1.1 keep-alive does not provide per-request response attribution beyond that ordering"）。`request.reusedSocket` 标记本次请求是否走了复用 socket（基于 `ECONNRESET` 的自动重试判断依据）。
- `maxSockets` 超限时官方语义："If the host attempts to open more connections than `maxSockets`, the additional requests will enter into a **pending request queue**"（`agent.requests` 可观测）。本 demo E3 实测：4 个并发 60ms 延迟请求在 `maxSockets=1` 下总耗时 252ms（排队串行 ≈ 4×60ms），`maxSockets=4` 下 80ms（并行）。
- `scheduling` 策略：`'lifo'` 选最近使用的 socket（低请求速率下降低"选中已被服务器关闭的 socket"风险），`'fifo'` 选最久未用的（高请求速率下最大化打开的 socket 数）。

### 空闲连接的生命周期（E4）

- 官方："Pooled connections have TCP Keep-Alive enabled for them, but **servers may still close idle connections**, in which case they will be removed from the pool and a new connection will be made when a new HTTP request is made for that host and port." 池中 socket 由 `keepSocketAlive` 默认实现 `setKeepAlive(true, keepAliveMsecs)` + `unref()`；再被 `reuseSocket` 时 `ref()`。
- E4 全流程实测：响应后 socket 进 `freeSockets` 池 → server `keepAliveTimeout` 到期关闭空闲连接（实测关闭点 ≈ 超时值 + ~1s 宽限）→ 客户端收 `'close'` 移除池项（官方 "Sockets are removed from an agent when the socket emits either a `'close'` event or an `'agentRemove'` event"）→ 下一请求新建 TCP 连接。
- **`agentKeepAliveTimeoutBuffer`**（默认 1000ms，v24.7.0+）：从 server 的 `keep-alive: timeout=...` hint 里减去的缓冲——保证 agent 抢在 server 之前关 socket。**推论（本 demo 开发期实测）**：把 server 的 `keepAliveTimeout` 设得很小（如 100ms），hint − buffer 已是过去时刻，agent 会**直接销毁 socket 而不进池**——于是"短 keepAliveTimeout 服务器"下 keep-alive 池化完全失效，形同每次重连。

### globalAgent 与显式 Agent（E2）

- 未指定 `agent` 时用 `http.globalAgent`；Node 19 起 globalAgent 默认 `keepAlive: true`（每次 Node 大版本升级要复查这类行为变化——v22.22.2 实测确认）。
- 官方最佳实践："It is good practice, to `destroy()` an `Agent` instance when it is no longer in use, because unused sockets consume OS resources."

## 对比

| 配置 | 5 串行请求 TCP 连接数(实测) | 适用 |
| --- | --- | --- |
| `keepAlive: true, maxSockets: 1` | **1**（reusedSocket=01111） | 对单一上游限流/保序 |
| `keepAlive: false` | **5** | 每请求隔离（官方：需要 per-request 隔离可传 `agent: false`） |
| `globalAgent`(Node≥19 默认) | 1（默认开 keep-alive） | 一般客户端请求 |

## 环境

- Node.js ≥ 16（本 demo 实测 v22.22.2 / Windows）；无第三方依赖
- 占用本地端口 18801 / 18802；E4 需约 4 秒等待窗口

## 运行方式

```bash
cd js && node main.js                              # 约 5 秒,9 项断言
cd ts && node --experimental-strip-types main.ts   # TS 版同组断言
```

## 关键代码

- `js/main.js`：本地起真实 http server 统计 TCP 连接数与 remotePort 集合——E1 keep-alive 复用（连接数=1、`reusedSocket` 序列 `01111`）；E2 无 keep-alive 对照 + globalAgent 默认值；E3 maxSockets 排队 vs 并行计时；E4 freeSockets 池化 → server 关空闲 → 池移除 → 重连。

## 性能边界

- keep-alive 省掉的是 TCP 三次握手 + 慢启动（内网 ~1ms、跨公网几十 ms）；高频短请求收益显著，长闲低频场景反而维护一堆空闲 socket。
- `maxSockets: Infinity` + 突发流量 = 连接风暴打爆对端 backlog；对每上游设上限是基本卫生。

## 注意事项与常见坑

- **server keepAliveTimeout 别设太小**：agent 的 `agentKeepAliveTimeoutBuffer`（默认 1000ms）会从 server hint 里扣除，hint 小于 buffer 时 socket 根本不进池（实测 100ms 超时 → `freeSockets` 恒空）。
- **server 关闭空闲连接的实际时刻 ≈ keepAliveTimeout + ~1s 宽限**（实测 2000ms 配置在 ~3s 关闭），测试等待窗口要留足。
- **`Connection: keep-alive` 头 ≠ agent 的 keepAlive 选项**（官方提醒勿混淆）；`keepAlive: false` 且 `maxSockets: Infinity` 时 agent 发 `Connection: close`。
- 复用连接上**响应按序配对、无逐请求归属**——同一连接上乱序处理响应是协议错误。
- 用完的 Agent 要 `destroy()`；池中未用 socket 是 `unref()` 的（不会阻止进程退出），但 OS fd 仍占着。

## 参考资料

- Node.js 官方文档 — HTTP（Class: http.Agent：职责、keepAlive/maxSockets/maxFreeSockets/scheduling/agentKeepAliveTimeoutBuffer 选项、freeSockets/requests/getName、keepSocketAlive/reuseSocket 默认实现、HTTP/1.1 响应按序配对）：<https://nodejs.org/api/http.html#class-httpagent>
