# STATE.md — 自动化巡检主状态文件

> 每轮结束追加/滚动,**永远 ≤ 5 KB**;历史全本见 `_docs/archive/`。
> 调度(每 2h,12 点/天)、配额(5 主 + ≤2 副)、失败回退:[`SCHEDULE_QUOTA.md`](./SCHEDULE_QUOTA.md);Token 控制:[`OPTIMIZATION.md` §2.4](./OPTIMIZATION.md)

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
```

> 索引表可动态增长:副任务拓展子类目时表末尾顺延编号,`next_index` 取模基数以实际行数为准(规则见 `AGENT_RULES.md` §一.5)。
> 大类轮换(硬约束):每轮顶层大类(`NN-` 前缀)必须 ≠ 上轮(看 §二 `last_top`);同大类则顺延到下一个不同大类索引,`next_index` 推进到选中索引+1,被跳过项不记 skip。

## 二、本轮状态

```
next_index     : 12    # 本轮选 12(03-系统编程/网络编程)≠ 02-Web开发 ✓(同大类 02 跳过 9-11);占位锁
last_run       : 2026-09-14 00:59   # 本轮占位锁(2026-09-13 22:42 距今 137 分钟 > 80 ✓)
last_top       : 03-系统编程       # 本轮顶层大类 — 选 12 ✓ ≠ 上轮 02-Web开发
skipped        : []
failed_attempts: []
```

## 三、最近 5 轮(滚动窗口)

| 时间 | 索引 | 主题摘要 | demo |
| --- | --- | --- | --- |
| 2026-09-13 22:42 | 8 | 前端框架:React Fiber 链表 + Hooks dispatcher/环链表 + Zustand vanilla store + Vite ESM/预构建/HMR + 虚拟 DOM Diff 双端 + LIS | +5 |
| 2026-09-13 20:34 | 0 | 服务端网络栈:kqueue BSD/macOS IO 多路复用 + Schmidt 1995 Reactor 模式 + TCP 长度前缀帧定界 + Protobuf wire format + sendfile(2) 零拷贝 | +5 |
| 2026-09-13 18:15 | 45 | 图数据库:属性图 LPG + Cypher 只读解析 + BFS/DFS/Dijkstra + PageRank + Neo4j 定长记录 | +5 |
| 2026-09-13 16:04 | 44 | Go 语言:goroutine+channel+select + slice+growslice + error %w+Is/As/Join + defer+panic+recover + map hmap+bmap+渐进式扩容 | +5 |
| 2026-09-13 13:46 | 41 | IaC:HCL 解析器 + Terraform 依赖图 Kahn + Ansible 幂等 + Jinja2 模板 + State diff & Plan | +5 |

> 超出 5 轮的细节见 `_docs/archive/schedule.md`(完整日志)+ `git log -p`(历史回溯)。

## 四、rotate 与查找

- rotate(副任务执行,不耗主配额):completed.md > 100 行 / schedule.md > 30 行 → 截到最近 100/30 行,丢弃部分查 `git log -p _docs/archive/`;写新数据时本文件只动 §三,archive append 即可
- 某 demo 是否做过 → Grep `completed.md`;上轮权威资料/坑 → Grep `schedule.md`;总数 → 数 completed.md 行数
- 本文件由巡检任务每轮末尾追加;人工编辑直接 Edit,大改同步 `OPTIMIZATION.md §2.4`。
