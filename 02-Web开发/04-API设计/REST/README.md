# REST

## 已完成 demo

- [x] [问题详情RFC9457/](./问题详情RFC9457/) — RFC 9457 `application/problem+json`：成员类型不符即忽略（含"JSON true 不是 number"这个坑）、相对 `type` 按 base 解析、`about:blank` 的 title SHOULD 等于状态短语、扩展名 SHOULD 规则、XML 数组用 `<i>`（Python 44 断言 + Go）
- [x] [条件请求与乐观并发/](./条件请求与乐观并发/) — RFC 9110 §13 + §8.8.3.2：强/弱比较 Table 3、**弱 ETag 下 `If-Match` 永远失败**、`If-None-Match: *` 防重复创建、六步优先级、基线非 2xx/412 时忽略全部条件、lost update（Python 50 断言 + Go）
- [x] [成熟度模型与方法语义/](./成熟度模型与方法语义/) — RMM 四层的可观测判据（多资源 / GET + 状态码 / 超媒体）+ safe/idempotent/cacheable + 代理 MUST NOT 重试非幂等请求 + `page?do=delete` 违规检查（Python 57 断言 + Go）

## 待研究

- [x] Richardson 成熟度模型
- [ ] HATEOAS 的表示形态（HAL / Siren / JSON:API）
- [ ] RESTful 资源建模与 URI 命名
- [ ] API 版本化策略
- [ ] 限流与 RateLimit 头部
