# 632 · Redis Sentinel：从主观下线到故障转移完成

> 归属：`07-数据存储/03-缓存`。Sentinel 是 Redis 官方的高可用组件，它的难点不在「检测不到」，
> 而在**多个哨兵如何在没有中心协调者的前提下就「谁来主刀」达成一致**。本 demo 按
> `redis/redis` unstable 分支 `src/sentinel.c` 实读，把 SDOWN/ODOWN 判定、纪元单调的领导者
> 选举、从节点挑选与七态故障转移状态机落成可执行代码（Python 可跑，Go 同构）。

## 一、事实来源（本轮实读，非凭记忆）

| 来源 | 拿到什么 |
| --- | --- |
| `src/sentinel.c` 47-56 | `SRI_S_DOWN (1<<3)` / `SRI_O_DOWN (1<<4)` / `SRI_MASTER_DOWN (1<<5)` / `SRI_FAILOVER_IN_PROGRESS (1<<6)` / `SRI_RECONF_SENT (1<<8)` / `SRI_RECONF_INPROG (1<<9)` / `SRI_RECONF_DONE (1<<10)` |
| `src/sentinel.c` 63-81 | `SENTINEL_PING_PERIOD 1000` / `SENTINEL_MAX_DESYNC 1000` / `SENTINEL_DEFAULT_PARALLEL_SYNCS 1`；`sentinel_info_period 10000` / `sentinel_publish_period 2000` / `sentinel_min_link_reconnect_period 15000` / `sentinel_slave_reconf_timeout 10000` / `sentinel_election_timeout 10000` / `sentinel_default_down_after 30000` / `sentinel_default_failover_timeout 60*3*1000` |
| `src/sentinel.c` 90-96 | 七态枚举与名称 |
| `src/sentinel.c` 4654-4684 | `sentinelCheckObjectivelyDown` |
| `src/sentinel.c` 4792-4817、4848-4910 | `sentinelVoteLeader` / `sentinelGetLeader` |
| `src/sentinel.c` 4991-5042 | `sentinelStartFailover` / `sentinelStartFailoverIfNeeded` |
| `src/sentinel.c` 5074-5145 | `compareSlavesForPromotion` / `sentinelSelectSlave` |
| `src/sentinel.c` 2748-2772 | INFO 解析驱动的 `SENT → INPROG → DONE` |
| `src/sentinel.c` 5150-5397 | 各状态处理函数与 `sentinelFailoverStateMachine` |
| `src/sentinel.c` 5404-5415 | `sentinelAbortFailover` |

## 二、核心机制

### 1. SDOWN 是「我觉得」，ODOWN 是「大家觉得」

```c
if (master->flags & SRI_S_DOWN) {
    quorum = 1; /* the current sentinel. */
    ... 数其他 sentinel 里带 SRI_MASTER_DOWN 的 ...
    if (quorum >= master->quorum) odown = 1;
}
```

**quorum 从 1 起算**（自己那一票），所以「quorum=2、共 3 个哨兵」意味着**只要再有一个同伴
同意**就够了，不是两个同伴。反过来，**自己不处于 SDOWN 时 quorum 恒为 0**，哪怕所有同伴都
喊 master 挂了也不会 ODOWN —— 自己的观测是必要条件。

### 2. 纪元单调 + 一纪元一票

```c
char *sentinelVoteLeader(sentinelRedisInstance *master, uint64_t req_epoch,
                         char *req_runid, uint64_t *leader_epoch) {
    if (req_epoch > sentinel.current_epoch) sentinel.current_epoch = req_epoch;
    if (master->leader_epoch < req_epoch && sentinel.current_epoch <= req_epoch) {
        master->leader = sdsnew(req_runid);
        master->leader_epoch = sentinel.current_epoch;      /* ← 不是 req_epoch */
        ...
        if (strcasecmp(master->leader,sentinel.myid))       /* 投给别人 */
            master->failover_start_time = mstime()+rand()%SENTINEL_MAX_DESYNC;
    }
    *leader_epoch = master->leader_epoch;
}
```

三处细节：

- **`leader_epoch` 记的是 `current_epoch` 而不是 `req_epoch`**。因为第一个 `if` 已经把
  `current_epoch` 抬到 `req_epoch`，两者通常相等；但请求落后时记的是旧值，读代码容易看错。
- 投票条件是 `master->leader_epoch < req_epoch && current_epoch <= req_epoch`。第一个 `if`
  之后 `current_epoch >= req_epoch` 恒成立，于是后半个条件等价于 **`current_epoch == req_epoch`**
  —— 这就是「一个纪元只投一票」的全部实现。
- **投给别人时会把自己的 `failover_start_time` 推后 `rand() % 1000` ms**（去同步），避免多个
  哨兵同时发起下一轮。

### 3. 当选要过两道门槛

```c
voters = dictSize(master->sentinels)+1;
voters_quorum = voters/2+1;
if (winner && (max_votes < voters_quorum || max_votes < master->quorum))
    winner = NULL;
```

必须**同时**满足：① 绝对多数（`voters/2+1`）；② 不少于配置的 `master->quorum`。
两个门槛取的是**更严的那个**：5 个哨兵、`quorum=2` 时 3 票即可；同样 5 个但 `quorum=5` 时，
3 票过了绝对多数却仍会被判「无人当选」。

还有一个反直觉之处：如果别的候选已经拿到多数票，**我会顺势把票投给它**（`myvote =
sentinelVoteLeader(master, epoch, winner, ...)`），而不是固执地投自己。这让选举能快速收敛。

### 4. 启动故障转移的三道门 + 冷却

```c
if (!(master->flags & SRI_O_DOWN)) return 0;                    /* 1. 必须 ODOWN */
if (master->flags & SRI_FAILOVER_IN_PROGRESS) return 0;         /* 2. 不能并发 */
if (mstime() - master->failover_start_time < master->failover_timeout*2)
    return 0;                                                    /* 3. 冷却 */
sentinelStartFailover(master);
```

冷却期是 **`failover_timeout * 2`**（默认 `180000 * 2 = 6 分钟`），不是 `failover_timeout` 本身。
起始时 `failover_start_time = now + rand()%1000`，所以「刚启动就检查」必然落在冷却里。

### 5. 七态状态机

```text
none → wait_start → select_slave → send_slaveof_noone → wait_promotion
     → reconf_slaves → update_config
```

- `wait_start`：反复问「我是不是 leader」；不是且超过 `min(election_timeout, failover_timeout)`
  → `-failover-abort-not-elected`。
- `select_slave`：选不到合格从节点 → `-failover-abort-no-good-slave`。
- `send_slaveof_noone` / `wait_promotion`：被提升者断连或迟迟不升主，超过 `failover_timeout` → 中止。
- `reconf_slaves`：逐个给其余从节点发 `SLAVEOF <new master>`，**并发受 `parallel_syncs`（默认 1）限制**。
- `update_config`：切换主节点地址，之后才是 `+switch-master`。

`abort` 只能发生在 `<= wait_promotion`；一旦被提升者确认了角色，故障转移**必须走完**。

### 6. 从节点挑选：priority → offset → runid

```c
if ((*sa)->slave_priority != (*sb)->slave_priority)
    return (*sa)->slave_priority - (*sb)->slave_priority;     /* 升序，小的优先 */
if ((*sa)->slave_repl_offset > (*sb)->slave_repl_offset) return -1;  /* 降序，大的优先 */
...
return strcasecmp(sa_runid, sb_runid);                        /* 升序，小的优先 */
```

`slave_priority == 0` 的从节点**永不入选**（配置里用来钉死「这台只做备份」）。
runid 为 NULL 视为**最大**（兼容不发布 runid 的老版本）。

过滤还有四道：SDOWN/ODOWN 剔除、链路断开剔除、`now - last_avail_time > ping_period*5` 剔除、
INFO 过期剔除。最后一道的窗口**会随 master 状态变化**：master 处于 SDOWN 时用 `ping_period*5`
（5 秒，因为此时每秒拉一次 INFO），否则用 `info_period*3`（30 秒）。

### 7. 重配置：状态机自己**不会**把它标记为完成

这是本 demo 最值得记住的一条。`RECONF_SENT → RECONF_INPROG → RECONF_DONE` 的迁移发生在
**INFO 解析函数**里（`sentinel.c:2748`），不在状态机里：

- `SENT → INPROG`：从节点 INFO 里的 `master_host/master_port` 已经等于被提升者；
- `INPROG → DONE`：从节点的 `master_link_status` 变成 up。

状态机侧只有两条**兜底**路径：

- `RECONF_SENT` 超过 `sentinel_slave_reconf_timeout`（10 s）→ 置 `DONE`。但注意源码在置完
  DONE 后**没有 `continue`**，会继续往下再发一次 `SLAVEOF` 并把 `SENT` 又置回来。
- `sentinelFailoverDetectEnd` 里 `elapsed > failover_timeout` → 强制收尾（`+failover-end-for-timeout`）。

另外 `while(in_progress < master->parallel_syncs && (de = dictNext(&di)) != NULL)` 的循环条件
**在取第一个元素之前就判**：`parallel_syncs=1` 且已有 1 个 in_progress 时，循环体一次都不跑，
上面那条 10 秒超时分支**根本进不去**。本 demo 用两条成对断言把这一点钉住。

## 三、运行

```bash
cd 07-数据存储/03-缓存/RedisSentinel故障转移/python
python selfcheck_sentinel.py   # 39 条断言：常量 / ODOWN / 投票 / 选举 / 启动门
python selfcheck_failover.py   # 41 条断言：选从 / 状态机 / 重配置
python main.py                 # 三节点集群从 SDOWN 走到 update_config
cd ../go && go run sentinel.go failover.go main.go
```

## 四、断言设计

- **ODOWN 成对**：只有自己 SDOWN → 不 ODOWN；自己 + 1 个同伴 → ODOWN；同伴撤回 → `-odown`。
  再加一条负控：**自己不 SDOWN 时，所有同伴都说挂了也不 ODOWN**。
- **一纪元一票**：同纪元第二次请求不改写 leader；更大纪元可改投；**更小**纪元被忽略。
- **投己 vs 投人成对**：投给自己时 `failover_start_time` 不动，投给别人时加去同步量。
- **双门槛分离**：过绝对多数且过 quorum → 当选；只过 quorum 不过多数 → 无人；过多数但
  `max_votes < quorum` → 无人。
- **冷却边界**：`now - start == failover_timeout*2` 才放行（严格小于才拦）。
- **INFO / last_avail 的窗口用「差值恰好等于阈值」与「阈值+1」成对钉**，确认是严格大于。
- **`parallel_syncs=1` 时超时分支不可达**，放开到 2 后才生效 —— 两条断言互为对照。

## 五、注意事项与口径

- 本 demo 不建模网络层：`is-master-down-by-addr` 的请求/应答、HELLO 消息、TILT 模式、
  脚本通知、配置重写（`sentinelFlushConfig`）都省略了。
- `sentinelSelectSlave` 用 `qsort` 排序，**非稳定排序**，三个键全相等时结果未定义；
  Python 侧用稳定排序 + 相同比较键，Go 侧用「严格小于才替换」的线性扫描，均属**口径声明**。
- 真实运行里投票通过 `SENTINEL is-master-down-by-addr` 携带 `req_epoch/runid` 交换；
  本 demo 直接调用 `sentinelVoteLeader`，语义等价但省掉了 IO。
- `SRI_LEADER (1<<17)` / `SRI_FORCE_FAILOVER (1<<18)` 等标志在源码里存在，本 demo 只在
  `abort` 与 `wait_start` 的强制分支用到 `SRI_FORCE_FAILOVER`。

## 六、参考资料（实际读过）

- <https://raw.githubusercontent.com/redis/redis/unstable/src/sentinel.c>
- <https://raw.githubusercontent.com/redis/redis/unstable/src/server.h>
