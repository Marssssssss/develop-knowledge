# API 设计

## 子领域

- [REST/](./REST/)
- [GraphQL/](./GraphQL/)
- [WebSocket/](./WebSocket/)
- [OpenAPI/](./OpenAPI/)
- [HATEOAS/](./HATEOAS/)
- [gRPC/](./gRPC/)
- [幂等性与重试/](./幂等性与重试/)

## 已完成 demo

- [x] [WebSocket/握手协议/](./WebSocket/握手协议/) — RFC 6455 §4 握手（HTTP Upgrade + SHA-1+GUID → `Sec-WebSocket-Accept` + 101）
- [x] [REST/问题详情RFC9457/](./REST/问题详情RFC9457/) — RFC 9457 problem details：成员类型校验 + 相对 URI 解析 + 扩展命名规则 + `about:blank` title 推导 + XML `<i>` 数组
- [x] [REST/条件请求与乐观并发/](./REST/条件请求与乐观并发/) — RFC 9110 §13：强/弱比较表 + If-Match/If-None-Match/If-Range + 六步优先级 + lost update
- [x] [REST/成熟度模型与方法语义/](./REST/成熟度模型与方法语义/) — RMM 四层可从报文反推 + safe/idempotent/cacheable + 谁可以自动重试
- [x] [GraphQL/N+1与DataLoader/](./GraphQL/N+1与DataLoader/) — N+1 的往返计数 + batchLoadFn 两条硬约束 + per-request memoization + 错误缓存语义
- [x] [幂等性与重试/](./幂等性与重试/) — Idempotency-Key（draft-07）：首次/重试/并发三态 + 400/422/409 分派 + fingerprint + 复合键
- [x] [HATEOAS/HAL-Siren-JSONAPI/](./HATEOAS/HAL-Siren-JSONAPI/) — 三形态对照：HAL 的 `_links`/`_embedded`/CURIE/模板 + Siren 的 actions/fields + JSON:API 的文档结构与 include/fields/sort 查询族（Py 118 断言）
- [x] [REST/API版本化与弃用/](./REST/API版本化与弃用/) — `Deprecation`（sf-date, RFC 9651 §3.3.7）vs `Sunset`（HTTP-date, RFC 8594）+ Accept/qvalue 协商与 Vary + RFC 9110 Errata 7306 + URI/媒体类型/头三种版本载体（Py 90 断言）
- [x] [OpenAPI/契约优先与3.1/](./OpenAPI/契约优先与3.1/) — OAS 3.1.1 + JSON Schema 2020-12 方言对齐 + discriminator 分支选取 + 3.0→3.1 迁移（nullable/boolean exclusive）+ contentMediaType（Py 114 断言）
- [x] [gRPC/Protobuf线格式与API建模/](./gRPC/Protobuf线格式与API建模/) — varint/ZigZag/tag/六种线类型 + packed 必须拼接 + group 字段号配对 + 字段号治理与 reserved 闭区间 + MergeFrom 合并语义 + gRPC 17 码与 (a)(b)(c) 重试准则（Py 113 断言）
- [x] [WebSocket/心跳与重连/](./WebSocket/心跳与重连/) — 控制帧三条硬约束（≤125B/不可分片/可插队）+ Ping/Pong 四条语义（原样回显/可只答最近一个/单向心跳）+ 关闭握手状态机 + §7.4 状态码区间与 1005/1006/1015 禁写 + gRPC 官方退避算法（Py 245 断言）

## 待研究

- [x] Richardson 成熟度模型
- [x] GraphQL N+1 问题
- [x] WebSocket 心跳与重连
- [x] HATEOAS 的表示形态（HAL / Siren / JSON:API）
- [ ] 限流与 RateLimit 头部（draft-ietf-httpapi-ratelimit-headers + GCRA）→ 见 `02-Web开发: 流量治理与限流`
- [x] API 版本化策略（URI / 头 / 内容协商）
- [x] OpenAPI 3.1 契约优先与代码生成
- [x] gRPC 与 Protocol Buffers 的 API 建模
- [ ] WebSocket 扩展协商（permessage-deflate / RFC 7692）
- [ ] HTTP/2 与 gRPC 的流控与 HPACK
- [ ] API 网关层的聚合与 BFF 模式
- [ ] 事件驱动 API（AsyncAPI / WebSub / SSE）

## 参考资料

- RFC 9457 *Problem Details for HTTP APIs* — <https://www.rfc-editor.org/rfc/rfc9457.txt>
- RFC 9110 *HTTP Semantics*（§8.8 校验器、§9.2 方法属性、§13 条件请求）— <https://www.rfc-editor.org/rfc/rfc9110.txt>
- Martin Fowler, *Richardson Maturity Model* — <https://martinfowler.com/articles/richardsonMaturityModel.html>
- graphql/dataloader 官方 README — <https://raw.githubusercontent.com/graphql/dataloader/main/README.md>
- draft-ietf-httpapi-idempotency-key-header-07 — <https://www.ietf.org/archive/id/draft-ietf-httpapi-idempotency-key-header-07.txt>
- RFC 8941 *Structured Field Values for HTTP* — <https://www.rfc-editor.org/rfc/rfc8941>
- RFC 6455 §5.4/§5.5/§7.4 *The WebSocket Protocol* — <https://www.rfc-editor.org/rfc/rfc6455.txt>
- IANA WebSocket Close Code Number Registry — <https://www.iana.org/assignments/websocket/close-code-number.csv>
- RFC 8594 *The Sunset HTTP Header Field* — <https://www.rfc-editor.org/rfc/rfc8594.txt>
- JSON:API v1.1 / HAL draft-kelly-json-hal-08 / Siren — <https://jsonapi.org/format/> · <http://stateless.co/hal_specification.html> · <https://github.com/kevinswiber/siren>
- OpenAPI Specification 3.1.1 — <https://spec.openapis.org/oas/v3.1.1.html>
- Protobuf Encoding / Proto3 Guide — <https://protobuf.dev/programming-guides/encoding/> · <https://protobuf.dev/programming-guides/proto3/>
- gRPC Status Codes / Connection Backoff — <https://grpc.io/docs/guides/status-codes/> · <https://github.com/grpc/grpc/blob/master/doc/connection-backoff.md>
