# 后端

## 子领域

| 子目录 | 语言/框架 |
| --- | --- |
| [Node.js/](./Node.js/) | Express / Fastify / NestJS |
| [Go-Web/](./Go-Web/) | net/http / Gin / Echo / Fiber |
| [Java-Spring/](./Java-Spring/) | Spring Boot / WebFlux |
| [Python-Web/](./Python-Web/) | Flask / FastAPI / Django |
| [优雅关闭与连接排空/](./优雅关闭与连接排空/) | Go net/http Shutdown/Close + Kubernetes Pod 终止 |
| [中间件与责任链/](./中间件与责任链/) | Django 洋葱模型 / Express arity 分派 |
| [HTTP缓存与条件请求/](./HTTP缓存与条件请求/) | RFC 9111 新鲜度 / Age / Vary + RFC 9110 §13 前置条件 |
| [HTTP2帧与流多路复用/](./HTTP2帧与流多路复用/) | RFC 9113 帧层与流控 + RFC 7541 HPACK |
| [JWT与JWS会话认证/](./JWT与JWS会话认证/) | RFC 7515 紧凑序列化 + RFC 7519 注册声明 |

## 已完成 demo

- [x] [Node.js/Event-Loop/](./Node.js/Event-Loop/) — Node.js 6 阶段 Event Loop + nextTick/Promise 微任务 (JS + TS)
- [x] [Node.js/Libuv线程池/](./Node.js/Libuv线程池/) — UV_THREADPOOL_SIZE 吞吐扩展 + 跨 API 传染 + 运行时设置无效 (JS + TS)
- [x] [Node.js/Stream背压/](./Node.js/Stream背压/) — write/drain/highWaterMark 阈值语义 + pipe 自动背压 (JS + TS)
- [x] [Node.js/Cluster与WorkerThreads/](./Node.js/Cluster与WorkerThreads/) — SCHED_NONE 倾斜 + disconnect vs kill + 结构化克隆 + transfer/SAB (JS + TS)
- [x] [Node.js/async_hooks与AsyncLocalStorage/](./Node.js/async_hooks与AsyncLocalStorage/) — hook 事件序 + Promise 因果链 + ALS 语义 + 迷你复刻 (JS + TS)
- [x] [Node.js/HTTP-Agent连接复用/](./Node.js/HTTP-Agent连接复用/) — keep-alive 复用 + maxSockets 排队 + freeSockets 池化 (JS + TS)
- [x] [优雅关闭与连接排空/](./优雅关闭与连接排空/) — Shutdown 只关 idle 而 Close 无差别全关 + 1ms 起翻倍夹 500ms 的轮询 + StateNew 5 秒阈值 + preStop 一次性 +2s (Python + Go)
- [x] [中间件与责任链/](./中间件与责任链/) — 洋葱请求正序响应逆序 + 短路让内层全部失效 + process_exception 逆序 + Express 靠 arity 4 识别错误处理器 + next('route') 仅 METHOD 栈 (Python + Go)
- [x] [HTTP缓存与条件请求/](./HTTP缓存与条件请求/) — s-maxage 仅共享缓存生效 + Expires 减 Date + 启发式 10% 且有显式过期即禁 + Age 三口径 + Vary 缺席只能对缺席 + 前置条件六步优先级 (Python + Go)
- [x] [HTTP2帧与流多路复用/](./HTTP2帧与流多路复用/) — 9 字节帧头 + MAX_FRAME_SIZE 区间 + 官方 -44KB 流控例 + HPACK 整数前缀编码 + 附录 C.3.1 字节序列 + 动态表条目 +32 (Python + Go)
- [x] [JWT与JWS会话认证/](./JWT与JWS会话认证/) — 签名输入是 ASCII(header.payload) + 官方 A.1 向量对拍 + exp 严格小于/nbf 大于等于 + aud 缺席即拒 + alg=none 靠白名单挡 (Python + Go)

## 共同的子知识点

- HTTP/1.1 vs HTTP/2 vs HTTP/3
- 中间件 / 拦截器机制
- ORM / 数据访问
- 认证（JWT / Session / OAuth2）
