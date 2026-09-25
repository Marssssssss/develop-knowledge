# 幂等发放:SET NX 与 Lua 原子性

> 道具补发、充值回调、邮件领取,都绕不开同一道题:**同一笔单子到达两次,只许发一次**。
> Redis 给的原语是 SET NX;给的整体性保证是脚本的原子执行。

## 1. 判重原语:SET NX(redis.io SET 命令页原文)

- **NX**:Only set the key if it does **not** already exist——第二次同键调用"什么都不做";
- **XX** 相反(仅当已存在);**GET** 选项可顺带取回旧值;
- 幂等键 = 业务单号(`idem:{order_id}`),配 **EX** 设置窗口。

## 2. 两步版与它的竞态窗口

```text
1) SET idem:o1 granted NX EX 3600     ← 占键
2) INCRBY coin:alice 100              ← 加币
```

第 1、2 步之间崩溃 → 键已占、币未加:玩家没拿到钱,但重放会被 NX 挡住。
要么接受这个窗口并做补偿对账,要么——

## 3. Lua 单脚本:官方原子性(脚本导论页原文)

> "Redis guarantees the script's atomic execution. While executing the script,
> **all server activities are blocked** during its entire runtime...
> all of the script's effects **either have yet to happen or had already happened**."

把"查幂等键 + 占键 + 加币"写进同一个 EVAL:服务器阻塞执行整个脚本,
不存在中间态。代价:脚本慢 = 全库慢,脚本里只放短小的关键路径。

## 4. 幂等窗口的取舍

幂等键的 EX = 窗口长度:太短,慢重放(队列积压、客户端重试风暴)会二次发放;
太长,占内存且"补单"要人工清键。窗口 = 重放可能拖多久 + 余量。

## 自检

`python python/idempotent.py` —— 5 项断言:NX 判重 /
三连重放只加一次 / Lua 版原子性语义 / 两步版的悬态窗口 /
EX 过期后可再发放。Go 侧 `go/idempotent.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [Redis — SET(NX/XX/GET/EX 语义)](https://redis.io/commands/set/)
- [Redis — Scripting with Lua(atomic execution 原文)](https://redis.io/docs/latest/develop/programmability/eval-intro/)
