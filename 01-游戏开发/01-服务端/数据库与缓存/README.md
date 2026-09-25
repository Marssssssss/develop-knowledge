# 游戏数据库与缓存

玩家档案、背包、排行榜、好友关系等持久化方案选型。

## 常见组合

| 用途 | 选型 |
| --- | --- |
| 玩家档案 | MySQL / PostgreSQL（行式） |
| 排行榜 | Redis Sorted Set |
| 背包/道具 | MySQL JSON 列 或 MongoDB |
| 好友关系 | Redis Set + Graph |
| 会话/Token | Redis |
| 战斗日志 | 时序库（InfluxDB / TDengine） |

## 子知识点
## 已完成 demo

| demo | 主题 |
| --- | --- |
| [Redis排行榜/](./Redis排行榜/) | ZRANK 0 基升序、同分二进制字典序、复合分先到先得、ZINCRBY |
| [幂等发放与SET-NX/](./幂等发放与SET-NX/) | NX 判重、重放幂等、Lua 原子性、两步版悬态、EX 窗口 |
| [缓存淘汰策略与近似LRU/](./缓存淘汰策略与近似LRU/) | 策略矩阵与 noeviction 默认、volatile 候选集、采样近似、命中率对照 |
| [MongoDB背包更新操作符/](./MongoDB背包更新操作符/) | $set 点路径建嵌套、$inc 三边界与 null 报错、位置 $ 只改第一组、建模取舍 |
| [RedisStream邮件队列/](./RedisStream邮件队列/) | XADD 自动 ID、消费组分摊不重复、pending 账本与 XACK 收口、崩溃恢复 |

## 待研究

- [ ] 玩家档案分库分表策略（按玩家 ID hash）
- [x] Redis 在排行榜中的工程实践 → 728 Redis排行榜 + 729 幂等发放 + 730 淘汰策略 + 732 Stream 队列（2026-09-25 10:00 槽首批）
- [ ] 数据回档与备份
- [ ] 防作弊的数据交叉校验