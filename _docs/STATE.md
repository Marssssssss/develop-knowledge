# STATE.md — 自动化巡检主状态文件

> 每轮结束追加/滚动,**永远 ≤ 5 KB**;历史全本见 `_docs/archive/`。
> 调度(12 条定点任务,每日 00:00…22:00 整点各一次,永不漂移)、配额(5 主 + ≤2 副)、失败回退:[`SCHEDULE_QUOTA.md`](./SCHEDULE_QUOTA.md);Token:[`OPTIMIZATION.md`](./OPTIMIZATION.md)

## 一、轮询顺序(2026-09-16 起:大类严格轮转)

**旧机制已废**:禁止按 priority_score/偏好挑领域(原「仅 ≠ 上轮大类」允许 A→B→A 横跳且表尾被系统性多抓)。

**大类循环**(固定顺序,取后指针 +1,回绕):
`01-游戏开发 → 02-Web开发 → 03-系统编程 → 04-移动开发 → 05-AI与机器学习 → 06-DevOps → 07-数据存储 → 08-安全 → 09-语言学习 → 10-逆向工程 → 11-性能分析`
每轮大类 = 循环[§二 `top_pos`],**11 大类各占 1/11 轮次,与子类目数量无关**。

**大类内子类目轮转**:取该大类在索引表的全部条目(表序)第 `sub_pos[大类]` 个;完成后 +1 mod 条目数,新子类目只追加表尾。

```text
轮询索引表(组内顺序即轮转顺序):
0-7 01-游戏开发: 服务端|渲染|UI|游戏引擎|物理|AI|音频|动画
8-11 02-Web开发: 前端框架|后端|数据库|API设计
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
57    10-逆向工程: 固件与嵌入式逆向
58    02-Web开发: WebAssembly
59-60 11-性能分析: 全链路性能|容量规划与性能建模(# 补登记)
61    11-性能分析: 数据库性能
62    11-性能分析: 网络与传输性能
```

> 39-62 类目拓展/补登记(S2+S3),新子类目顺延 63 起。
## 二、本轮状态

```
top_pos        : 1         # 下轮消费循环[1]=02-Web开发(本轮已消费[0]=01-游戏开发)
sub_pos        : 01:6 02:2 03:2 04:2 05:7 06:6 07:1 08:3 09:2 10:3 11:3  # 下轮 sub_pos[01]=6 → 07-音频(本轮已消费 5 = 06-AI 游戏AI)
last_run       : 2026-09-22 01:05  # 00:00 槽收尾(开局 00:00:50)
last_top       : 01-游戏开发  # 本轮;索引 5 = 06-AI 游戏AI
skipped        : []
failed_attempts: []
```

## 三、最近 5 轮(滚动窗口)

| 时间 | 索引 | 主题摘要 | 推进 | 累计 | 来源与质量 | notify |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-09-22 00:01 | 5 | 01-游戏开发/06-AI:行为树·GOAP反向规划·效用系统·JPS·层级状态机HFSM | S3 | +5 | 550 | BT.CPP+ReGoap+big-brain+JPS AAAI11+W3C SCXML;Py 210 | notify: ok(errcode=0) |
| 2026-09-21 22:46 | 47 | 11-性能分析/基准测试方法论第三批:分配口径·预热判定·趋势分片·容器标定·参数化比较 | S1+S3 | +5 | 545 | jmh GCProfiler/Defaults/Warmup/Param+go benchmark.go/cgroup.go/proc.go+pyperf3+treeherder+catapult+proc.rst+hyperfine3;Py 211 | notify: ok |
| 2026-09-21 18:54 | 44 | 09-语言学习/Golang第四批:定时器四叉堆·netpoll信号量·连续栈增长·UTF-8查表·方法集与选择器 | S1+S3 | +5 | 536 | runtime/time.go+netpoll.go+stack.go+proc.go+utf8.go+spec+blog/strings;Py 371 | notify: ok |
| 2026-09-21 16:52 | 33 | 08-安全/02-网络安全第三批:TCP盲注入·DNS缓存投毒·证书透明度·SSH传输层·IPv6隐私地址 | S3 | +5 | 531 | RFC5961/5452/9162/4253/8308/4941/8981+Linux tcp_input.c;Py 1253 | notify: ok |
| 2026-09-21 14:48 | 27 | 07-数据存储/01-关系型第四批:分片与键空间·EXPLAIN 读数·并行 worker·autovacuum 与 XID 回绕·分区裁剪 | S3 | +5 | 526 | PG18 文档 6 篇+源码 5 份+Vitess+Citus/TiDB;Py 244 | notify: ok |

> 更早细节见 `archive/schedule.md`。

## 四、rotate 与查找

- rotate(副任务,不耗主配额):completed.md > 100 / schedule.md > 30 → 截到最近 100/30,丢弃部分查 `git log -p _docs/archive/`
- 某 demo 是否做过 → Grep `completed.md`;上轮资料/坑 → Grep `schedule.md`;总数 → 数 completed.md 行数
- 每轮只动 §三,超 5 KB 时压缩旧行。
