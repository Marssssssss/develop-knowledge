# Redis 排行榜:ZSET 语义与同分决胜

> 排行榜是 Redis Sorted Set 的教科书场景,但"并列名次怎么办"这一问,
> 官方语义给出的答案往往与业务想要的不一样——**同分默认按成员字节序排**。
> 理解这条规则,才知道为什么工程上要把时间戳编进 score。

## 1. 官方语义三件事

- **ZRANK 是 0 基、升序**:rank 0 = 最低分;排行榜用的"第 1 名"视图是
  **ZREVRANK**/`ZREVRANGE`(反序);
- **同分决胜**(ZADD 页「Elements with the same score」节):
  分数是第一关键字,**同分元素之间按二进制字典序**排(把成员当字节数组比较);
- **ZINCRBY**:已有成员累加、新成员从 0 起步——榜单实时刷新的主通道。

## 2. 平局规则的冲突

| 规则 | 谁定义 | 排出来的结果 |
| --- | --- | --- |
| 二进制字典序 | Redis 默认 | adam < mia < zoe(与达成时间无关) |
| 先到先得 | 业务常见诉求 | 更早达成同分者在前 |

默认规则**不可配置**——既不是先到先得,也不是随机。

## 3. 工程答案:复合分

```text
composite = score × TS_MAX + (TS_MAX − 1 − timestamp)
```

- 高位放真实分数,低位放"越早越大"的时间补偿;
- 同分时更早达成者复合分更大——平局规则改写,**不新增任何命令**;
- 代价:分数不再可直接读(展示前要除以 TS_MAX)、精度受 double 限制
  (分数与时间戳的位数预算要设计)。

## 自检

`python python/leaderboard.py` —— 4 项断言:rank 0 基升序与 None /
同分按成员字节序的默认序 / 复合分实现先到先得 / ZINCRBY 累加语义。
Go 侧 `go/leaderboard.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [Redis — ZADD(Elements with the same score 节)](https://redis.io/commands/zadd/)
- [Redis — ZRANK(0-based,升序)](https://redis.io/commands/zrank/)
- [Redis — ZINCRBY](https://redis.io/commands/zincrby/)
