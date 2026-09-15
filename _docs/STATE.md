# STATE.md — 自动化巡检主状态文件

> 每轮结束追加/滚动,**永远 ≤ 5 KB**;历史全本见 `_docs/archive/`。
> 调度(**12 条定点任务**:每日 `00:00 02:00 … 22:00` 整点各触发一次,永不漂移,见 [`AUTOMATION_PROMPT.md` §〇](./AUTOMATION_PROMPT.md))、配额(5 主 + ≤2 副)、失败回退:[`SCHEDULE_QUOTA.md`](./SCHEDULE_QUOTA.md);Token 控制:[`OPTIMIZATION.md` §2.4](./OPTIMIZATION.md)

## 一、轮询顺序

按 `priority_score = base_score - done_count * 10 - last_done_days * 0.5`,每轮取最高分。

```text
轮询索引表(next_index 控制下次起点):
0: 01-游戏开发/服务端  1: 01-游戏开发/渲染
2: 01-游戏开发/UI  3: 01-游戏开发/游戏引擎
4: 01-游戏开发/物理  5: 01-游戏开发/AI
6: 01-游戏开发/音频  7: 01-游戏开发/动画
8: 02-Web开发/前端框架  9: 02-Web开发/后端
10: 02-Web开发/数据库  11: 02-Web开发/API设计
12: 03-系统编程/网络编程  13: 03-系统编程/进程线程协程
14: 03-系统编程/内存管理  15: 03-系统编程/文件系统
16: 04-移动开发/iOS  17: 04-移动开发/Android
18: 04-移动开发/跨平台  19: 05-AI与机器学习/深度学习
20: 05-AI与机器学习/强化学习  21: 05-AI与机器学习/LLM
22: 05-AI与机器学习/计算机视觉  23: 06-DevOps/容器化
24: 06-DevOps/CI-CD  25: 06-DevOps/监控
26: 06-DevOps/Kubernetes  27: 07-数据存储/关系型
28: 07-数据存储/NoSQL  29: 07-数据存储/缓存
30: 07-数据存储/搜索引擎  31: 08-安全/密码学
32: 08-安全/Web安全  33: 08-安全/网络安全
34: 09-语言学习/Python  35: 10-逆向工程/二进制逆向
36: 10-逆向工程/移动端逆向  37: 11-性能分析/系统级剖析
38: 11-性能分析/应用级剖析  39: 05-AI与机器学习/经典机器学习
40: 05-AI与机器学习/语音与多模态  41: 06-DevOps/IaC与配置管理
42: 06-DevOps/SRE与可靠性工程  43: 06-DevOps/DevSecOps(# 目录 07-)
44: 09-语言学习/Golang  45: 07-数据存储/图数据库(Neo4j/Cypher)
46: 10-逆向工程/协议逆向  47: 11-性能分析/基准测试方法论
48: 05-AI与机器学习/特征工程与降维  49: 05-AI与机器学习/概率图模型
50: 09-语言学习/Rust
```

> 39-50 为类目自动拓展新增(2026-09-12 起,S2);表尾编号即当前行数,新子类目顺延 51 起。
> 索引表动态增长:取模基数按实际行数(`AGENT_RULES.md` §一.5);大类轮换(硬约束):顶层大类须 ≠ 上轮 `last_top`,同大类顺延;`next_index` 推进到选中索引+1,跳过项不记 skip。

## 二、本轮状态

```
next_index     : 48    # 48 本轮 05-AI与机器学习/特征工程与降维(进行中,占位锁)
last_run       : 2026-09-15 10:05
last_top       : 05-AI与机器学习   # ≠ 上轮 11-性能分析
skipped        : []
failed_attempts: []
```

## 三、最近 5 轮(滚动窗口)

| 时间 | 索引 | 主题摘要 | 推进 | 累计 |
| --- | --- | --- | --- | --- |
| 2026-09-15 08:14 | 47 | 基准测试方法论首批 5 demo:benchstat 非参数比较(中位数 CI + U 检验)、JMH perfnorm 硬件归因(22 事件)、统计检验选择(Welch/ARE)、CI 回归门禁(step fit + 多重比较)、Gregg 主动基准 7 清单;S2=+2 子类目(04-全链路/05-容量规划) | +5 | 201 |
| 2026-09-15 06:29 | 46 | 协议逆向第一批:NW/SW 对齐切字段、BinaryInferno 熵边界+单调启发、Veritas P-PSM、JA3/JA4(官方向量复现)、Polyglot 四启发式;S3=索引同步+rotate | +5 | 196 |
| 2026-09-15 04:31 | 45 | 图数据库第二批:量化路径、Bolt 握手+PackStream、并发控制+等待图死锁、火山模型、MERGE 语义;S3 | +5 | 191 |
| 2026-09-15 02:27 | 44 | Golang 第二批:interface/itable、GPM、三色 GC+混合屏障、context、泛型推断;S3 | +5 | 186 |
| 2026-09-15 00:14 | 41 | IaC 第二批:state 锁、Ansible 22 级、Vault 格式、Pulumi 引擎、GitOps SSA;S2=+1 | +5 | 181 |

> 超出 5 轮的细节见 `_docs/archive/schedule.md`(完整日志)+ `git log -p`(历史回溯)。

## 四、rotate 与查找

- rotate(副任务执行,不耗主配额):completed.md > 100 行 / schedule.md > 30 行 → 截到最近 100/30 行,丢弃部分查 `git log -p _docs/archive/`;写新数据时本文件只动 §三,archive append 即可
- 某 demo 是否做过 → Grep `completed.md`;上轮权威资料/坑 → Grep `schedule.md`;总数 → 数 completed.md 行数
- 本文件由巡检任务每轮末尾追加;§三 每行须压到单行 ≤120 字,新增时同步压缩旧行以守住 ≤5 KB;人工编辑直接 Edit,大改同步 `OPTIMIZATION.md §2.4`。
