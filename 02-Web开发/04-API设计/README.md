# API 设计

## 子领域

- [REST/](./REST/)
- [GraphQL/](./GraphQL/)
- [WebSocket/](./WebSocket/)
- [幂等性与重试/](./幂等性与重试/)

## 已完成 demo

- [x] [WebSocket/握手协议/](./WebSocket/握手协议/) — RFC 6455 §4 握手（HTTP Upgrade + SHA-1+GUID → `Sec-WebSocket-Accept` + 101）
- [x] [REST/问题详情RFC9457/](./REST/问题详情RFC9457/) — RFC 9457 problem details：成员类型校验 + 相对 URI 解析 + 扩展命名规则 + `about:blank` title 推导 + XML `<i>` 数组
- [x] [REST/条件请求与乐观并发/](./REST/条件请求与乐观并发/) — RFC 9110 §13：强/弱比较表 + If-Match/If-None-Match/If-Range + 六步优先级 + lost update
- [x] [REST/成熟度模型与方法语义/](./REST/成熟度模型与方法语义/) — RMM 四层可从报文反推 + safe/idempotent/cacheable + 谁可以自动重试
- [x] [GraphQL/N+1与DataLoader/](./GraphQL/N+1与DataLoader/) — N+1 的往返计数 + batchLoadFn 两条硬约束 + per-request memoization + 错误缓存语义
- [x] [幂等性与重试/](./幂等性与重试/) — Idempotency-Key（draft-07）：首次/重试/并发三态 + 400/422/409 分派 + fingerprint + 复合键

## 待研究

- [x] Richardson 成熟度模型
- [x] GraphQL N+1 问题
- [ ] WebSocket 心跳与重连
- [ ] HATEOAS 的表示形态（HAL / Siren / JSON:API）
- [ ] 限流与 RateLimit 头部（draft-ietf-httpapi-ratelimit-headers + GCRA）→ 见 `02-Web开发: 流量治理与限流`
- [ ] API 版本化策略（URI / 头 / 内容协商）
- [ ] OpenAPI 3.1 契约优先与代码生成
- [ ] gRPC 与 Protocol Buffers 的 API 建模

## 参考资料

- RFC 9457 *Problem Details for HTTP APIs* — <https://www.rfc-editor.org/rfc/rfc9457.txt>
- RFC 9110 *HTTP Semantics*（§8.8 校验器、§9.2 方法属性、§13 条件请求）— <https://www.rfc-editor.org/rfc/rfc9110.txt>
- Martin Fowler, *Richardson Maturity Model* — <https://martinfowler.com/articles/richardsonMaturityModel.html>
- graphql/dataloader 官方 README — <https://raw.githubusercontent.com/graphql/dataloader/main/README.md>
- draft-ietf-httpapi-idempotency-key-header-07 — <https://www.ietf.org/archive/id/draft-ietf-httpapi-idempotency-key-header-07.txt>
- RFC 8941 *Structured Field Values for HTTP* — <https://www.rfc-editor.org/rfc/rfc8941>
