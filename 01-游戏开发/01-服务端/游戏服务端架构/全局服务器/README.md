# 全局服务器(Global Server)与平台服进程组

## 简介

- 分服(shard)架构里每个区服是独立世界,但**登录、全区排行、跨区路由、角色名唯一、转区、运营邮件**必须有一个所有区服都连着的中心 —— 这就是 MMO 的「平台服」进程组。本 demo 实现 GlobalServer 的最小内核:区服注册/心跳摘除、全局在线表与消息路由、全区排行榜、rolereg 名字唯一、两阶段转区、idip 离线邮件队列。
- 关键概念:
  - **平台服 vs 普通服**:平台服处理全局功能(登录 flserver、全局 globalserver、角色唯一 roleregserver、转区 rolechangeserver、运营 idipserver);普通服(gateway/scene/record)只管本区。
  - **epoch 防旧实例**:区服重启后 epoch+1,旧实例的迟到心跳/消息按 epoch 拒绝,防止「幽灵区」。
  - **两阶段转区**:锁定 → 源区序列化移交 → 目标区落地 ack → 解锁;中途卡死超时回滚。

## 原理详解

### 进程拓扑(参考来源归纳)

```
Client ──> flserver(login: 验证、发 token、按负载选区)
        ──> gatewayserver(客户端与所有后端进程之间的桥)
             ├─> sceneserver(本区玩法) ──> recordserver(落库)
             └─> globalserver ── roleregserver / rolechangeserver / idipserver
```

gamedev.net 的经典三层表述:每游戏一个 Auth Server;每 shard 一个 Character Server + 若干 Zone Server;zone 换图时**由 zone 告诉客户端下一个 zone 的地址**再重连 —— 跨区跳转和本 demo 的转区同构。

### 全局在线表与路由

- `uid -> (zone_id, gateway_id)`:私聊/踢人/跨区邮件先查表,在线则投目标 zone 收件箱,离线则落 offline_mail;
- zone 心跳超时摘除时,**该区玩家一并从在线表移除**,其消息自动转离线邮件(demo 断言 4 覆盖)。

### 两阶段转区(状态机)

```
free --transfer_request(锁)--> locked --stage2(源区除名+移交)--> inflight --ack(目标区上线)--> free
                                       |                                        |
                                       +--- 超时(目标 crash 等) <--- recover ---+
```

- 锁定期内**拒绝重复转区请求**;stage2 时源区立即除名 —— 移交期间玩家不在任何在线表,杜绝「双在线双写」;
- 卡死(如目标区 crash)超过锁超时 → 回滚清锁,玩家可重新发起。

### 全区排行榜

各区把分数更新推给 global,global 维护 top-N(每次插入后截断);量级上去后换 Redis ZSET(分片榜 + 定期归并),本 demo 演示归并语义。

## 对比 / 选型

| 方案 | 一致性 | 可用性 | 说明 |
| --- | --- | --- | --- |
| 单点 GlobalServer(本 demo) | 强 | 单点 | 小规模够用;失联摘除逻辑必须稳 |
| GlobalServer + 从(热备) | 强(切换窗口弱) | 高 | 切主时 epoch 机制防旧主复活 |
| 去中心化(区服 Gossip 互查) | 最终 | 高 | 跨区路由收敛慢,大世界 MMO 少用 |

## 环境准备

- OS 任意;Python 3.8+ / Go 1.18+,纯标准库;时间用注入的整数毫秒驱动。

## 运行方式

```bash
python3 python/main.py   # 25 项断言:login 选区/名字唯一/路由/摘除/榜单/转区
go run go/main.go
```

## 关键代码片段(Python)

```python
def sweep(self, now):
    """失联 zone 摘除:其在线玩家降级为离线(消息转 offline_mail)。"""
    for z in list(self.zones.values()):
        if z.up and now - z.last_hb > ZONE_HB_TIMEOUT_MS:
            z.up = False
            for uid in list(z.players):        # 该区玩家一并除名
                z.players.discard(uid)
                if self.online.get(uid, (None,))[0] == z.id:
                    del self.online[uid]

def zone_heartbeat(self, zid, epoch, load, now):
    if epoch != z.epoch:      # 旧实例迟到心跳:按 epoch 拒绝
        return "stale"
```

## 性能与边界

- 在线表/心跳全内存,单进程支撑 10 万级在线无压力;瓶颈在跨区消息扇入 —— 真实系统在 global 前再加一层按 uid 分片的路由服;
- top-N 榜每次全量排序 O(N log N),demo 规模用堆/跳表替换;
- 转区移交的玩家数据量决定 stage2 时长,锁超时要 > 最大移交时间,否则会活锁(反复回滚重发)。

## 注意事项与常见坑

- **摘除 zone 必须同步清它的玩家**,否则在线表指向尸体,消息永远投不出去;
- **epoch 不能省**:区服 crash 重启后旧实例的迟到包(心跳/转区 ack)会把状态打回过去;
- **转区锁要幂等**:重复请求、重复 ack、ack 迟到(已回滚后到达)都要安全拒绝 —— demo 断言 6/7 覆盖前两者;
- **登录选区按 (load, id) 排序**:键要稳定,否则并列时选择抖动,玩家反复被分到不同区。

## 参考资料(实际阅读过的权威来源)

- [mmo 游戏服务器架构简述 — Ftworld21](https://blog.csdn.net/Ftworld21/article/details/101601284) — 平台服/普通服进程分工原表:flserver / dbaccessserver / roleregserver / rolechangeserver / globalserver / idipserver / gatewayserver / sceneserver。
- [MMO Server Layout Design — gamedev.net](https://gamedev.net/forums/topic/549288-mmo-server-layout-design/) — Auth/Character/Zone 三层拓扑与「换图时 zone 告诉客户端下一个 zone 地址」的跳转流程。
- [《游戏服务端编程实践》1.2.2 MMO 架构模式](https://plumephp.com/game-server-in-action-122/) — 登录签发 token、世界服全局路由表(在线表)、断线重连 Token+快照恢复、跨区一致性策略。
- [Dedicated Server for MMO Hosting(2026)](https://bestdedicatedwebhostingserver.com/blog/dedicated-server-for-mmo-game-hosting) — Login/World/Zone/DB 各层的资源画像与并发规模参考。
