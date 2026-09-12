# 07 数据存储

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-关系型/](./01-关系型/) | MySQL / PostgreSQL / 索引 / 事务 |
| [02-NoSQL/](./02-NoSQL/) | MongoDB / Cassandra / DynamoDB |
| [03-缓存/](./03-缓存/) | Redis / Memcached |
| [04-搜索引擎/](./04-搜索引擎/) | Elasticsearch / Meilisearch |

## 已完成 demo

- [x] Elasticsearch 倒排索引与近实时搜索 — 见 [04-搜索引擎/elasticsearch/](./04-搜索引擎/elasticsearch/)（Python / Go）
- [x] MVCC 多版本并发控制 — 见 [01-关系型/MVCC/](./01-关系型/MVCC/)（C / Python / Go）
- [x] WAL 预写日志 + ARIES 恢复 — 见 [01-关系型/WAL/](./01-关系型/WAL/)（C / Python / Go）
- [x] 两阶段锁 2PL + 死锁检测 — 见 [01-关系型/2PL/](./01-关系型/2PL/)（C / Python / Go）

## 待研究

- [ ] MySQL InnoDB B+ 树索引（已迁到 `02-Web开发/03-数据库/B+树索引/`）
- [ ] MVCC 多版本并发控制（已在 `01-关系型/MVCC/` 完成）
- [ ] Redis 持久化（RDB / AOF）
- [ ] ES BM25 评分算法细节
- [ ] 隔离级别与幻读防（next-key lock / SSI）