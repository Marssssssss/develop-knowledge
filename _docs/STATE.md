# STATE.md — 自动化巡检主状态文件

> 每轮结束追加/滚动,**永远 ≤ 5 KB**;历史全本见 `_docs/archive/`。
> 调度(1.5h×2 任务合成 16 点/天)、配额(5 主 + ≤2 副)、失败回退:[`SCHEDULE_QUOTA.md`](./SCHEDULE_QUOTA.md);Token 控制:[`OPTIMIZATION.md` §2.4](./OPTIMIZATION.md)

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
next_index     : 40    # 本轮 38 同大类(11-性能分析)顺延至 39 05-经典机器学习;next → 40(05-语音与多模态)
last_run       : 2026-09-13 10:05
last_top       : 05-AI与机器学习   # 本轮顶层大类(大类轮换约束用,见 §一)
skipped        : []
failed_attempts: []
```

## 三、最近 5 轮(滚动窗口)

| 时间 | 索引 | 主题(细节见 archive/schedule.md) | demo |
| --- | --- | --- | --- |
| 2026-09-13 06:43 | 37 | 采样剖析原理(ITIMER_PROF/SIGPROF/99Hz)+ 火焰图生成(folded→SVG,flamegraph.pl 复现)+ perf_events(三模式/IPC/-F vs -c)+ strace与ptrace(TRACESYSGOOD/enter-exit 配对/Yama)+ Off-CPU分析(offcputime 记账/时间膨胀/--state=2) | +5 |
| 2026-09-13 03:28 | 35 | ELF 文件解析(elf(5))+ PE 文件解析(MS 官方规范)+ x64 调用约定(psABI)+ GOT/PLT 延迟绑定(Taylor)+ YARA 静态特征匹配 | +5 |
| 2026-09-13 00:29 | 34 | Python 五大语言机制:生成器 yield / 上下文管理器 / 描述符协议 / 协程与 asyncio / 元类与 __init_subclass__ | +5 |
| 2026-09-12 21:39 | 33 | TLS 1.3 握手 + SYN Cookie 防 SYN Flood + eBPF/XDP 包过滤 + IKEv2-ESP 协商 + DNSSEC 链式信任 | +5 |
| 2026-09-12 18:36 | 32 | XSS 与 CSP + CSRF Token + SQL 注入与预编译 + JWT 验证 + Same-Origin 与 CORS | +5 |

> 超出 5 轮的细节见 `_docs/archive/schedule.md`(完整日志)+ `git log -p`(历史回溯)。

## 四、rotate 与查找

- rotate(副任务执行,不耗主配额):completed.md > 100 行 / schedule.md > 30 行 → 截到最近 100/30 行,丢弃部分查 `git log -p _docs/archive/`;写新数据时本文件只动 §三,archive append 即可
- 某 demo 是否做过 → Grep `completed.md`;上轮权威资料/坑 → Grep `schedule.md`;总数 → 数 completed.md 行数
- 本文件由巡检任务每轮末尾追加;人工编辑直接 Edit,大改同步 `OPTIMIZATION.md §2.4`。
