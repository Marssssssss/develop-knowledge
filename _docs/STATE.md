# STATE.md — 自动化巡检主状态文件

> 每轮结束追加/滚动,**永远 ≤ 5 KB**;历史全本见 `_docs/archive/`。
> 调度(**12 条定点任务**:每日 `00:00…22:00` 整点各一次,永不漂移,见 [`AUTOMATION_PROMPT.md`](./AUTOMATION_PROMPT.md))、配额(5 主 + ≤2 副)、失败回退:[`SCHEDULE_QUOTA.md`](./SCHEDULE_QUOTA.md);Token:[`OPTIMIZATION.md`](./OPTIMIZATION.md)

## 一、轮询顺序(2026-09-16 起:大类严格轮转)

**旧机制已废**:禁止按 priority_score/偏好挑领域(原「仅 ≠ 上轮大类」允许 A→B→A 横跳且表尾被系统性多抓)。

**大类循环**(固定顺序,取后指针 +1,回绕):
`01-游戏开发 → 02-Web开发 → 03-系统编程 → 04-移动开发 → 05-AI与机器学习 → 06-DevOps → 07-数据存储 → 08-安全 → 09-语言学习 → 10-逆向工程 → 11-性能分析`
每轮大类 = 循环[§二 `top_pos`],**11 大类各占 1/11 轮次,与子类目数量无关**。

**大类内子类目轮转**:取该大类在索引表的全部条目(表序)第 `sub_pos[大类]` 个;完成后 +1 mod 条目数,新子类目只追加表尾。

```text
轮询索引表(组内按 | 顺序即索引递增;仅供大类内子类目轮转取条目):
0-7   01-游戏开发: 服务端|渲染|UI|游戏引擎|物理|AI|音频|动画
8-11  02-Web开发: 前端框架|后端|数据库|API设计
12-15 03-系统编程: 网络编程|进程线程协程|内存管理|文件系统
16-18 04-移动开发: iOS|Android|跨平台
19-22 05-AI与机器学习: 深度学习|强化学习|LLM|计算机视觉
23-26 06-DevOps: 容器化|CI-CD|监控|Kubernetes
27-30 07-数据存储: 关系型|NoSQL|缓存|搜索引擎
31-33 08-安全: 密码学|Web安全|网络安全
34    09-语言学习: Python
35-36 10-逆向工程: 二进制逆向|移动端逆向
37-38 11-性能分析: 系统级剖析|应用级剖析
39-40 05-AI与机器学习: 经典机器学习|语音与多模态
41-43 06-DevOps: IaC与配置管理|SRE与可靠性工程|DevSecOps(# 目录 07-)
44    09-语言学习: Golang
45    07-数据存储: 图数据库(Neo4j/Cypher)
46    10-逆向工程: 协议逆向
47    11-性能分析: 基准测试方法论
48-49 05-AI与机器学习: 特征工程与降维|概率图模型
50    09-语言学习: Rust
51    06-DevOps: 平台工程
52    05-AI与机器学习: RAG与向量检索
53    09-语言学习: TypeScript
54    08-安全: 应用安全与供应链
55    04-移动开发: 推送与消息(APNs/FCM/厂商通道)
56    02-Web开发: 流量治理与限流(RateLimit 头部/GCRA)
```

> 39-56 为类目自动拓展新增(S2);新子类目顺延 57 起。条目数 8/5/4/4/9/8/5/4/4/3/3 = 57(02-Web开发 因新增 56 由 4 变 5)。

## 二、本轮状态

```
top_pos        : 2         # 下轮取循环[2]=03-系统编程(本轮已消费循环[1]=02-Web开发)
sub_pos        : 01:4 02:1 03:0 04:0 05:5 06:4 07:4 08:1 09:0 10:1 11:1  # 本轮消费 sub_pos[02]=0 → 索引 8 前端框架 → 1(5 条目)
last_run       : 2026-09-20 06:46  # 06:00 槽收尾(开局 06:00:46);保险 45 分钟;上轮 04:38 距 82 分钟通过
last_top       : 02-Web开发  # 本轮消费循环[1]
skipped        : []
failed_attempts: []
```

## 三、最近 5 轮(滚动窗口)

| 时间 | 索引 | 主题摘要 | 推进 | 累计 | 来源与质量 | notify |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-09-20 06:46 | 8 | 02-Web开发/前端框架第三批:Svelte runes·RSC Flight·渲染管线·WebComponents·ESM实时绑定 | S3 | +5 | 446 | Svelte runes 4 篇+ReactFlightClient 源码+MDN+WHATWG+ECMA262 全 curl 直抓;Py 141 断言 | notify: pending |
| 2026-09-20 04:38 | 3 | 01-游戏开发/游戏引擎首批:Archetype存储·固定时间步·JobSystem与Burst·Nanite·Godot节点生命周期 | S3 | +5 | 441 | Unity 官方 13 页+Nanite SIGGRAPH PDF 155 页+Godot 4 页+Gaffer 全文;Py 133 断言 | notify: ok |
| 2026-09-20 02:55 | 37 | 11-性能分析/系统级剖析第四批:%CPU口径与IPC·RPS/RFS/XPS与中断亲和·OffWake归因·差分火焰图·BPF ringbuf | S1+S3 | +5 | 436 | Gregg CPU-Util-is-Wrong/Off-CPU/差分 + 内核 scaling/ringbuf;Py 163 断言 | notify: ok |
| 2026-09-19 22:32 | 53 | 09-语言学习/TypeScript首批:结构类型·条件类型分配律·映射修饰符·收窄守卫·方差 | S1+S3 | +5 | 426 | TS Handbook 5篇+4.7公告;21 fixture+Py 53 断言 | notify: ok |
| 2026-09-19 21:04 | 31 | 08-安全/密码学深水区:TLS1.3·国密SM2/3/4·BLS·ML-KEM·Groth16 | S1+S3 | +5 | 421 | RFC8446/8998+FIPS203+论文+OpenSSL/GmSSL/kyber;Py 146 断言 | notify: ok |

> 更早细节见 `archive/schedule.md`。

## 四、rotate 与查找

- rotate(副任务,不耗主配额):completed.md > 100 行 / schedule.md > 30 行 → 截到最近 100/30,丢弃部分查 `git log -p _docs/archive/`;本文件只动 §三,archive append 即可
- 某 demo 是否做过 → Grep `completed.md`;上轮资料/坑 → Grep `schedule.md`;总数 → 数 completed.md 行数
- 每轮只动 §三且每行 ≤120 字,超 5 KB 时同步压缩旧行;大改同步 `OPTIMIZATION.md §2.4`。
