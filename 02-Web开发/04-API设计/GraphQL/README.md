# GraphQL

## 已完成 demo

- [x] [N+1与DataLoader/](./N+1与DataLoader/) — 1+N 与 1+1 的往返计数（三层 21 vs 3）、batchLoadFn 的"等长 + 索引对齐"两条硬约束、同帧去重（官方在 `load()` 时就缓存 promise）、`cache:false` 时 keys 重复、Error 实例被缓存而 throw 不缓存（JS 23 断言 node 实跑 + Python 22 断言）

## 待研究

- [x] DataLoader 解决 N+1
- [ ] Schema 优先 vs Code 优先
- [ ] Subscription（实时）
- [ ] Persisted Query 与查询白名单
- [ ] 查询复杂度与深度限制（防滥用）
