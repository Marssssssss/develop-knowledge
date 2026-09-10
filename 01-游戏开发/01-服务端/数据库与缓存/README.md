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

- [ ] 玩家档案分库分表策略（按玩家 ID hash）
- [ ] Redis 在排行榜中的工程实践
- [ ] 数据回档与备份
- [ ] 防作弊的数据交叉校验