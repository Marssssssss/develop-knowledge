# 流量治理与限流

「限流」这件事拆开是三层：**计量**（用什么算法数请求）、**表达**（怎么把限额告诉客户端）、**部署**（单机的内存状态如何在多副本间共享）。本目录 5 个 demo 各占一层。

## 已完成 demo

| demo | 层次 | 核心机制 |
| --- | --- | --- |
| [01-GCRA与漏桶计量/](./01-GCRA与漏桶计量/) | 计量 | ITU-T I.371 虚拟调度 GCRA（TAT / 发射间隔 T / 容限 τ）+ 连续状态漏桶的等价性 + 时间桶的边界双倍突发 |
| [02-RateLimit响应头部/](./02-RateLimit响应头部/) | 表达 | draft-ietf-httpapi-ratelimit-headers-11：`RateLimit-Policy` 的 `q/qu/w/pk` + `RateLimit` 的 `r/t/pk` + 三种 problem type + Retry-After 优先级 |
| [03-令牌桶与预热限流/](./03-令牌桶与预热限流/) | 计量 | Guava `SmoothBursty` / `SmoothWarmingUp` 的 `storedPermits` 折算、梯形积分代价、冷因子 3.0、两种相反的初态 |
| [04-滑动窗口计数/](./04-滑动窗口计数/) | 计量 | Sentinel `LeapArray` 环形数组四分支、严格大于的过期判据、边界突发被压到 1× |
| [05-描述符匹配与分布式配额/](./05-描述符匹配与分布式配额/) | 部署 | envoyproxy/ratelimit：描述符树三条候选匹配 + 深度必须相等 + `hits_addend` 与 `shadow_mode` + 确定性缓存键 |

## 三条贯穿性的结论

1. **计量器的差别全在「如何对待过去的欠使用」**。Guava 用 `storedPermits` 把它折算成等待时间（突发型折成 0、预热型折成梯形面积）；GCRA 干脆不维护桶，只维护一个标量 TAT 加一只时钟，闲置时零开销。
2. **「恰好等于阈值」这一格的选择是各实现最不一致的地方**。GCRA 规范原文用严格大于、工程实现普遍用 `>=`；Sentinel 的过期判据是严格大于；Envoy 的过限判据是严格大于但统计记账用 `>=`。写断言时必须逐个确认，不能想当然。
3. **限流信息的表达与执行是解耦的**。规范（draft-11）只定义字段语义与客户端优先级链，不规定任何限流行为——服务端 `MAY` 在任意状态码下返回、`MUST NOT` 保证 `r>0` 就一定放行。

## 待研究

- [ ] 分布式限流的原子性与一致性（Redis Lua / INCR+EXPIRE 竞态、多副本时钟漂移）
- [ ] 自适应限流与过载保护（注：`11-性能分析/05-容量规划与性能建模/过载保护与自适应并发/` 已有 Envoy + Netflix Vegas 的 demo）
- [ ] 排队与优先级队列（criticality 分级的准入控制）
- [ ] 限流的可观测性（near-limit 指标、被拒请求的归因）
- [ ] 客户端侧退避（指数退避 + 抖动 + 熔断协同）
- [ ] 多租户配额与配额借调（burst credit / quota borrowing）
- [ ] 计费型限流（quota_mode：先扣后还、失败返还）

## 参考资料

- draft-ietf-httpapi-ratelimit-headers-11, *RateLimit header fields for HTTP* — <https://www.ietf.org/archive/id/draft-ietf-httpapi-ratelimit-headers-11.txt>
- RFC 6585 §4, *429 Too Many Requests* — <https://www.rfc-editor.org/rfc/rfc6585.txt>
- Wikipedia, *Generic cell rate algorithm*（转述 ITU-T I.371）— <https://en.wikipedia.org/wiki/Generic_cell_rate_algorithm>
- Brandur Leach, *Rate Limiting, Cells, and GCRA* — <https://brandur.org/rate-limiting>
- google/guava@master `SmoothRateLimiter.java` — <https://raw.githubusercontent.com/google/guava/master/guava/src/com/google/common/util/concurrent/SmoothRateLimiter.java>
- alibaba/Sentinel@master `LeapArray.java` — <https://raw.githubusercontent.com/alibaba/Sentinel/master/sentinel-core/src/main/java/com/alibaba/csp/sentinel/slots/statistic/base/LeapArray.java>
- envoyproxy/ratelimit@main `config_impl.go` — <https://raw.githubusercontent.com/envoyproxy/ratelimit/main/src/config/config_impl.go>
- envoyproxy/ratelimit@main `base_limiter.go` — <https://raw.githubusercontent.com/envoyproxy/ratelimit/main/src/limiter/base_limiter.go>
