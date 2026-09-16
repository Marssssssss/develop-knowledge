# Node.js

## 已完成 demo

- [x] [Event-Loop/](./Event-Loop/) — Node.js 6 阶段 Event Loop + nextTick/Promise 微任务优先级 (JS + TS)
- [x] [Libuv线程池/](./Libuv线程池/) — UV_THREADPOOL_SIZE 吞吐扩展 + 跨 API 传染(fs/crypto/dns.lookup/zlib 共池)+ 运行时设置无效 (JS + TS)
- [x] [Stream背压/](./Stream背压/) — write()/drain/highWaterMark 阈值语义 + pipe 自动背压 + unpipe 后 data 不恢复流动 (JS + TS)
- [x] [Cluster与WorkerThreads/](./Cluster与WorkerThreads/) — SCHED_NONE 连接倾斜实测 + disconnect vs kill + 结构化克隆原型丢失 + transfer/SAB (JS + TS)
- [x] [async_hooks与AsyncLocalStorage/](./async_hooks与AsyncLocalStorage/) — Timeout 事件序 + Promise 因果链 + ALS 六项语义 + 迷你 ALS 复刻 (JS + TS)
- [x] [HTTP-Agent连接复用/](./HTTP-Agent连接复用/) — keep-alive 1 连接复用 + maxSockets 排队 + freeSockets 池化与 server 空闲超时交互 (JS + TS)

## 待研究

- [ ] undici 与全局 fetch 的连接管理
- [ ] Stream 高阶 API（pipeline / web streams / async iterator）
- [ ] V8 堆与 GC（--max-old-space-size / 堆快照诊断）
