# STATE.md — 自动化巡检主状态文件

> 每轮结束追加/滚动,**永远 ≤ 5 KB**;历史全本见 `_docs/archive/`。
> 调度(**12 条定点任务**:每日 `00:00 02:00 … 22:00` 整点各触发一次,永不漂移,见 [`AUTOMATION_PROMPT.md` §〇](./AUTOMATION_PROMPT.md))、配额(5 主 + ≤2 副)、失败回退:[`SCHEDULE_QUOTA.md`](./SCHEDULE_QUOTA.md);Token 控制:[`OPTIMIZATION.md` §2.4](./OPTIMIZATION.md)

## 一、轮询顺序

按 `priority_score = base_score - done_count * 10 - last_done_days * 0.5`,每轮取最高分。

```text
轮询索引表(next_index 控制下次起点):
0  : 01-游戏开发/服务端
1  : 01-游戏开发/渲染
2  : 01-游戏开发/UI
3  : 01-游戏开发/游戏引擎
4  : 01-游戏开发/物理
5  : 01-游戏开发/AI
6  : 01-游戏开发/音频
7  : 01-游戏开发/动画
8  : 02-Web开发/前端框架
9  : 02-Web开发/后端
10 : 02-Web开发/数据库
11 : 02-Web开发/API设计
12 : 03-系统编程/网络编程
13 : 03-系统编程/进程线程协程
14 : 03-系统编程/内存管理
15 : 03-系统编程/文件系统
16 : 04-移动开发/iOS
17 : 04-移动开发/Android
18 : 04-移动开发/跨平台
19 : 05-AI与机器学习/深度学习
20 : 05-AI与机器学习/强化学习
21 : 05-AI与机器学习/LLM
22 : 05-AI与机器学习/计算机视觉
23 : 06-DevOps/容器化
24 : 06-DevOps/CI-CD
25 : 06-DevOps/监控
26 : 06-DevOps/Kubernetes
27 : 07-数据存储/关系型
28 : 07-数据存储/NoSQL
29 : 07-数据存储/缓存
30 : 07-数据存储/搜索引擎
31 : 08-安全/密码学
32 : 08-安全/Web安全
33 : 08-安全/网络安全
34 : 09-语言学习/Python     # 按语言组织,与按领域的 01-08 不同
35 : 10-逆向工程/二进制逆向   # 2026-09-12 用户指定新增顶层
36 : 10-逆向工程/移动端逆向
37 : 11-性能分析/系统级剖析     # 2026-09-12 新增顶层(Agent 判断放置)
38 : 11-性能分析/应用级剖析
39 : 05-AI与机器学习/经典机器学习   # 2026-09-12 巡检类目拓展新增(S2)
40 : 05-AI与机器学习/语音与多模态   # 2026-09-12 巡检类目拓展新增(S2)
41 : 06-DevOps/IaC与配置管理       # 2026-09-12 巡检类目拓展新增(S2)
42 : 06-DevOps/SRE与可靠性工程     # 2026-09-12 巡检类目拓展新增(S2)
43 : 06-DevOps/DevSecOps           # 2026-09-12 巡检类目拓展新增(S2,07-DevSecOps 目录)
44 : 09-语言学习/Golang             # 2026-09-12 巡检类目拓展新增(S2)
45 : 07-数据存储/图数据库             # 2026-09-12 巡检类目拓展新增(S2,Neo4j/Cypher/属性图占位)
46 : 10-逆向工程/协议逆向             # 2026-09-14 巡检类目拓展新增(S2,NetT/ExeT 两族 + JA3/JA4 指纹,目录 03-协议逆向)
```

> 索引表可动态增长:副任务拓展子类目时表末顺延编号,取模基数按实际行数(`AGENT_RULES.md` §一.5)。
> 大类轮换(硬约束):顶层大类须 ≠ 上轮 `last_top`,同大类顺延;`next_index` 推进到选中索引+1,跳过项不记 skip。

## 二、本轮状态

```
next_index     : 36    # 35 已完成 → 36
last_run       : 2026-09-14 18:01   # 占位锁
last_top       : 10-逆向工程   # ≠ 上轮 09-语言学习
skipped        : []
failed_attempts: []
```

## 三、最近 5 轮(滚动窗口)

| 时间 | 索引 | 主题摘要 | demo |
| --- | --- | --- | --- |
| 2026-09-14 18:01 | 35 | 二进制逆向第二批:变长指令编码(prefix/REX/ModRM/SIB/RIP 相对)+ ptrace 断点四步(0xCC→SIGTRAP→恢复+RIP 回退→单步→重埋)+ 栈溢出与 ROP(ret=pop rip/NX 边界/CET SHSTK/16B 对齐)+ Mach-O(8 字节对齐/lc_str 相对偏移/节号从 1 起)+ CFG 与结构化反编译(支配树/回边/follow 节点);S2 新增 `10-逆向工程/03-协议逆向`(索引 46) | +5 |
| 2026-09-14 17:11 | 34 | Python 运行时:字节码与 PEP 659 专用化 + PEP 634 模式匹配 + 分代 GC/immortal/PEP 442 + 导入机制(PEP 451/420)+ GIL 与自由线程(PEP 703) | +5 |
| 2026-09-14 14:30 | 31 | 密码学第二批:SHA-256 + HMAC-SHA256 + X25519 ECDH + ChaCha20-Poly1305 AEAD + Argon2id | +5 |
| 2026-09-14 10:26 | 27 | 关系型第二批:4 级隔离 × 4 异常矩阵 + InnoDB midpoint LRU 3/8 + WAL-before-data + PG CBO(直方图/DP/GEQO)+ 2PC recovery + 扩展协议 + PgBouncer | +5 |
| 2026-09-14 08:03 | 23 | 容器化第二批:OverlayFS copy-up/whiteout + Capabilities 5 集合/41 cap + Seccomp-BPF + veth pair/bridge/MASQUERADE + OCI Runtime Spec | +5 |

> 超出 5 轮的细节见 `_docs/archive/schedule.md`(完整日志)+ `git log -p`(历史回溯)。

## 四、rotate 与查找

- rotate(副任务执行,不耗主配额):completed.md > 100 行 / schedule.md > 30 行 → 截到最近 100/30 行,丢弃部分查 `git log -p _docs/archive/`;写新数据时本文件只动 §三,archive append 即可
- 某 demo 是否做过 → Grep `completed.md`;上轮权威资料/坑 → Grep `schedule.md`;总数 → 数 completed.md 行数
- 本文件由巡检任务每轮末尾追加;§三 每行须压到单行 ≤120 字,新增时同步压缩旧行以守住 ≤5 KB;人工编辑直接 Edit,大改同步 `OPTIMIZATION.md §2.4`。
