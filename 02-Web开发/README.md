# 02 Web 开发

最大的开发领域之一，涵盖前端、后端、数据库、API 设计等。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-前端框架/](./01-前端框架/) | React / Vue / Signals / SSR-Hydration / 构建工具 / 状态管理 |
| [02-后端/](./02-后端/) | Node.js / Go / Java / Python |
| [03-数据库/](./03-数据库/) | SQL & ORM / NoSQL |
| [04-API设计/](./04-API设计/) | REST / GraphQL / WebSocket / OpenAPI / HATEOAS / gRPC |
| [05-流量治理与限流/](./05-流量治理与限流/) | GCRA 与漏桶计量 / RateLimit 响应头部 / 令牌桶与预热 / 滑动窗口计数 / 描述符匹配与分布式配额 |
| [06-WebAssembly/](./06-WebAssembly/) | 最小模块手工解码(magic/段/LEB128/栈机);待:JS API / 验证阶段 / 组件模型(2026-09-25 19:00 槽开线,索引表第 58 项) |

## 已完成 demo

- [x] [01-前端框架/Vue/reactive/](./01-前端框架/Vue/reactive/) — Vue 3 Proxy 响应式最小实现 (TS + JS)
- [x] [01-前端框架/React/Fiber/](./01-前端框架/React/Fiber/) — Fiber 链表 + 双缓冲 + render/commit 两阶段(JS + TS)
- [x] [01-前端框架/React/Hooks/](./01-前端框架/React/Hooks/) — Hook 链表 + dispatcher + setState 环链表(JS + TS)
- [x] [01-前端框架/React/VirtualDOM-Diff/](./01-前端框架/React/VirtualDOM-Diff/) — 双端指针 + key 哈希 + LIS 乱序最小移动(JS + TS)
- [x] [01-前端框架/状态管理/Zustand/](./01-前端框架/状态管理/Zustand/) — 极简 vanilla store + useStore selector(JS + TS)
- [x] [01-前端框架/构建工具/Vite/](./01-前端框架/构建工具/Vite/) — ESM 冷启动 + 依赖预构建 + on-demand transform + HMR(JS + TS)
- [x] [01-前端框架/React/Concurrent/](./01-前端框架/React/Concurrent/) — Lane 位掩码 + 优先级换算 + 5ms 时间切片 + 饥饿防护 + transition 中断丢弃(JS + TS)
- [x] [01-前端框架/Vue/编译器优化/](./01-前端框架/Vue/编译器优化/) — 静态提升 + PatchFlags + Block Tree + 事件缓存(JS + TS)
- [x] [01-前端框架/Signals/](./01-前端框架/Signals/) — Computed 四状态机 + Watcher 三状态 + glitch-free 求值(JS + TS)
- [x] [01-前端框架/SSR-Hydration/](./01-前端框架/SSR-Hydration/) — 流式 SSR shell + boundary 标记 + 选择性水合(JS + TS)
- [x] [01-前端框架/构建工具/Turbopack/](./01-前端框架/构建工具/Turbopack/) — turbo-tasks:value cell + 读时依赖跟踪 + 内容相等短路(JS + TS)
- [x] [02-后端/Node.js/Event-Loop/](./02-后端/Node.js/Event-Loop/) — Node.js 6 阶段 Event Loop + nextTick/Promise 微任务 (JS + TS)
- [x] [02-后端/Node.js/Libuv线程池/](./02-后端/Node.js/Libuv线程池/) — 线程池吞吐扩展/跨API传染/运行时设置无效 (JS + TS)
- [x] [02-后端/Node.js/Stream背压/](./02-后端/Node.js/Stream背压/) — write/drain/hwm 阈值 + pipe 自动背压 + unpipe 反直觉 (JS + TS)
- [x] [02-后端/Node.js/Cluster与WorkerThreads/](./02-后端/Node.js/Cluster与WorkerThreads/) — SCHED_NONE 倾斜 + disconnect/kill + 结构化克隆 + transfer/SAB (JS + TS)
- [x] [02-后端/Node.js/async_hooks与AsyncLocalStorage/](./02-后端/Node.js/async_hooks与AsyncLocalStorage/) — hook 事件序 + Promise 因果链 + ALS 六项语义 (JS + TS)
- [x] [02-后端/Node.js/HTTP-Agent连接复用/](./02-后端/Node.js/HTTP-Agent连接复用/) — keep-alive 复用 + maxSockets 排队 + freeSockets 池化 (JS + TS)
- [x] [03-数据库/B+树索引/](./03-数据库/B+树索引/) — B+ 树索引原理 (M-way + 叶子兄弟链 + copy-up/push-up 分裂 + borrow/merge 重平衡) (C + Python + Go)
- [x] [03-数据库/SQL与ORM/N+1与预取策略/](./03-数据库/SQL与ORM/N+1与预取策略/) — 懒加载触发 + selectin/joined/subquery 策略语句数与结果等价性 (Python + Go)
- [x] [03-数据库/SQL与ORM/EXPLAIN执行计划解读/](./03-数据库/SQL与ORM/EXPLAIN执行计划解读/) — 代价模型 + 节点树解析 + loops 换算 + rows removed (Python + Go)
- [x] [03-数据库/SQL与ORM/连接池调优/](./03-数据库/SQL与ORM/连接池调优/) — `core*2+spindles` 公式 + pool-locking 下界 + 小池饱和曲线 (Python + Go)
- [x] [03-数据库/SQL与ORM/键集分页/](./03-数据库/SQL与ORM/键集分页/) — OFFSET O(N) vs keyset O(log N) + 翻页一致性 (Python + Go)
- [x] [03-数据库/NoSQL/Redis数据结构底层/](./03-数据库/NoSQL/Redis数据结构底层/) — dict 渐进式 rehash + zskiplist span 排名 (Python + Go)
- [x] [04-API设计/WebSocket/握手协议/](./04-API设计/WebSocket/握手协议/) — WebSocket 握手协议 RFC 6455 §4 (HTTP Upgrade + SHA-1+GUID → Sec-WebSocket-Accept + 101 Switching Protocols) (C + Python + Go)
- [x] [04-API设计/REST/问题详情RFC9457/](./04-API设计/REST/问题详情RFC9457/) — RFC 9457 problem details：成员类型不符即忽略 + 相对 type 解析 + `about:blank` title 推导 + 扩展命名规则 (Python + Go)
- [x] [04-API设计/REST/条件请求与乐观并发/](./04-API设计/REST/条件请求与乐观并发/) — RFC 9110 §13 条件请求：强/弱比较 + 六步优先级 + If-Range 精确匹配 + lost update 防护 (Python + Go)
- [x] [04-API设计/REST/成熟度模型与方法语义/](./04-API设计/REST/成熟度模型与方法语义/) — RMM 四层可观测判据 + safe/idempotent/cacheable + 代理禁止重试非幂等请求 (Python + Go)
- [x] [04-API设计/GraphQL/N+1与DataLoader/](./04-API设计/GraphQL/N+1与DataLoader/) — N+1 往返计数 (1+N vs 1+1) + batchLoadFn 等长/对齐约束 + per-request memoization (JavaScript + Python)
- [x] [04-API设计/幂等性与重试/](./04-API设计/幂等性与重试/) — Idempotency-Key draft-07：首次/重试/并发三态 + 400/422/409 分派 + fingerprint + 复合键隔离 (Python + Go)
- [x] [04-API设计/HATEOAS/HAL-Siren-JSONAPI/](./04-API设计/HATEOAS/HAL-Siren-JSONAPI/) — HAL 的 `_links`/`_embedded`/CURIE/URI 模板 + Siren 的 actions/fields/EffectiveMethod + JSON:API 文档结构与 include/fields/sort 查询族 (Python + Go)
- [x] [04-API设计/REST/API版本化与弃用/](./04-API设计/REST/API版本化与弃用/) — `Deprecation`(sf-date, RFC 9651 §3.3.7) vs `Sunset`(HTTP-date, RFC 8594) + Accept/qvalue 协商与 Vary + RFC 9110 Errata 7306 + 三种版本载体 (Python + Go)
- [x] [04-API设计/OpenAPI/契约优先与3.1/](./04-API设计/OpenAPI/契约优先与3.1/) — OAS 3.1.1 + JSON Schema 2020-12 方言对齐 + discriminator 分支选取 + 3.0→3.1 迁移 + contentMediaType (Python + Go)
- [x] [04-API设计/gRPC/Protobuf线格式与API建模/](./04-API设计/gRPC/Protobuf线格式与API建模/) — varint/ZigZag/tag/六种线类型 + packed 必须拼接 + group 字段号配对 + 字段号治理 + MergeFrom 合并语义 + gRPC 17 码与重试准则 (Python + Go)
- [x] [04-API设计/WebSocket/心跳与重连/](./04-API设计/WebSocket/心跳与重连/) — 控制帧 ≤125B/不可分片/可插队 + Ping/Pong 原样回显与单向心跳 + 关闭握手状态机 + §7.4 状态码区间与 1005/1006/1015 禁写 + gRPC 官方退避算法 (Python + Go)

- [x] [01-前端框架/Svelte/编译式响应性/](./01-前端框架/Svelte/编译式响应性/) — Svelte 5 runes 三层订阅 + pull 式 derived + 相等短路 + 深代理不 mutate 原对象 (Python + Go)
- [x] [01-前端框架/React/RSC与Flight协议/](./01-前端框架/React/RSC与Flight协议/) — Flight 行状态机 + `$` 引用前缀表 + 流式 `$L` 回填 (Python + Go)
- [x] [01-前端框架/浏览器渲染管线/](./01-前端框架/浏览器渲染管线/) — 改动分类 / 层提升 / 强制同步布局 / 图片迟到引发 reflow (Python + Go)
- [x] [01-前端框架/WebComponents/](./01-前端框架/WebComponents/) — 升级与 reaction 队列 / 构造期限制 / connectedMoveCallback (Python + Go)
- [x] [01-前端框架/模块系统/ESM实时绑定/](./01-前端框架/模块系统/ESM实时绑定/) — Link/Evaluate 两阶段 + live binding + TDZ 与循环依赖 (Python + Go)
- [x] [02-后端/优雅关闭与连接排空/](./02-后端/优雅关闭与连接排空/) — Go Shutdown/Close 分野 + 轮询 1ms→500ms + k8s Pod 终止宽限与 preStop 延期 (Python + Go)
- [x] [02-后端/中间件与责任链/](./02-后端/中间件与责任链/) — Django 洋葱短路语义 + process_view/process_exception 顺序 + Express arity 与 next('route') (Python + Go)
- [x] [02-后端/HTTP缓存与条件请求/](./02-后端/HTTP缓存与条件请求/) — RFC 9111 新鲜度/Age/Vary/stale 许可 + RFC 9110 §13.2.2 前置条件优先级 (Python + Go)
- [x] [02-后端/HTTP2帧与流多路复用/](./02-后端/HTTP2帧与流多路复用/) — RFC 9113 帧头与 SETTINGS/流控 + RFC 7541 HPACK 官方向量对拍 (Python + Go)
- [x] [02-后端/JWT与JWS会话认证/](./02-后端/JWT与JWS会话认证/) — RFC 7515 紧凑序列化与官方 A.1 向量 + RFC 7519 exp/nbf/aud 校验语义 (Python + Go)
- [x] [03-数据库/SQL与ORM/ORM会话与工作单元/](./03-数据库/SQL与ORM/ORM会话与工作单元/) — 身份映射弱引用 + 快照脏检查 + flush 三层顺序（先保存后删除、表内 UPDATE 先于 INSERT）(Python + Go)
- [x] [03-数据库/SQL与ORM/批量写入与Upsert/](./03-数据库/SQL与ORM/批量写入与Upsert/) — PG 唯一索引推断与 cardinality violation + SQLite UPSERT 差异 + REPLACE 的先删后插 (Python + Go)
- [x] [03-数据库/NoSQL/JSONB半结构化与索引/](./03-数据库/NoSQL/JSONB半结构化与索引/) — json/jsonb 规范化差异 + @> 越级不算 + GIN 两个操作符类与 pending list (Python + Go)
- [x] [03-数据库/SQL与ORM/行级安全与多租户/](./03-数据库/SQL与ORM/行级安全与多租户/) — USING/WITH CHECK 双向 + default-deny + 属主绕过与 FORCE + permissive OR / restrictive AND (Python + Go)
- [x] [03-数据库/列式存储与Parquet/](./03-数据库/列式存储与Parquet/) — Dremel def/rep levels 切分与装配 + RLE/Bit-Packing 混合编码逐字节对拍 (Python + Go)
- [x] [05-流量治理与限流/01-GCRA与漏桶计量/](./05-流量治理与限流/01-GCRA与漏桶计量/) — I.371 虚拟调度 GCRA(TAT/T/τ) 与连续状态漏桶等价 + 时间桶边界双倍突发 + 被拒不推进 TAT (Python + Go)
- [x] [05-流量治理与限流/02-RateLimit响应头部/](./05-流量治理与限流/02-RateLimit响应头部/) — draft-11 的 q/qu/w/pk 与 r/t/pk 校验 + Retry-After 优先级链 + 三种 problem type (Python + Go)
- [x] [05-流量治理与限流/03-令牌桶与预热限流/](./05-流量治理与限流/03-令牌桶与预热限流/) — Guava storedPermits 折算 + WarmingUp 梯形积分与 coldFactor=3.0 + 两种相反的初态 (Python + Go)
- [x] [05-流量治理与限流/04-滑动窗口计数/](./05-流量治理与限流/04-滑动窗口计数/) — Sentinel LeapArray 环形四分支 + 严格大于的过期判据 + 边界突发压到 1× (Python + Go)
- [x] [05-流量治理与限流/05-描述符匹配与分布式配额/](./05-流量治理与限流/05-描述符匹配与分布式配额/) — envoyproxy/ratelimit 三条候选匹配 + 深度必须相等 + hits_addend 与 shadow_mode + 确定性缓存键 (Python + Go)

## 状态

- 前端：Web 生态碎片化严重，优先沉淀**原理级** demo（虚拟 DOM、Diff 算法、Reactive 原理）
- 后端：每种语言写一个**最小 HTTP 服务 + 简单 CRUD**，便于对比
  - 进程/运行时模型先各做一个底层原理 demo（Event Loop / Goroutine 调度 / JVM 线程模型），再上框架