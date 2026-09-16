# 后端

## 子领域

| 子目录 | 语言/框架 |
| --- | --- |
| [Node.js/](./Node.js/) | Express / Fastify / NestJS |
| [Go-Web/](./Go-Web/) | net/http / Gin / Echo / Fiber |
| [Java-Spring/](./Java-Spring/) | Spring Boot / WebFlux |
| [Python-Web/](./Python-Web/) | Flask / FastAPI / Django |

## 已完成 demo

- [x] [Node.js/Event-Loop/](./Node.js/Event-Loop/) — Node.js 6 阶段 Event Loop + nextTick/Promise 微任务 (JS + TS)
- [x] [Node.js/Libuv线程池/](./Node.js/Libuv线程池/) — UV_THREADPOOL_SIZE 吞吐扩展 + 跨 API 传染 + 运行时设置无效 (JS + TS)
- [x] [Node.js/Stream背压/](./Node.js/Stream背压/) — write/drain/highWaterMark 阈值语义 + pipe 自动背压 (JS + TS)
- [x] [Node.js/Cluster与WorkerThreads/](./Node.js/Cluster与WorkerThreads/) — SCHED_NONE 倾斜 + disconnect vs kill + 结构化克隆 + transfer/SAB (JS + TS)
- [x] [Node.js/async_hooks与AsyncLocalStorage/](./Node.js/async_hooks与AsyncLocalStorage/) — hook 事件序 + Promise 因果链 + ALS 语义 + 迷你复刻 (JS + TS)
- [x] [Node.js/HTTP-Agent连接复用/](./Node.js/HTTP-Agent连接复用/) — keep-alive 复用 + maxSockets 排队 + freeSockets 池化 (JS + TS)

## 共同的子知识点

- HTTP/1.1 vs HTTP/2 vs HTTP/3
- 中间件 / 拦截器机制
- ORM / 数据访问
- 认证（JWT / Session / OAuth2）
