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
top_pos        : 7         # 下轮消费循环[7]=08-安全(本轮已消费[6]=07-数据存储)
sub_pos        : 01:6 02:3 03:3 04:3 05:8 06:7 07:2 08:3 09:2 10:3 11:3  # 本轮 sub_pos[07]=1 → 索引 28=02-NoSQL;07 共 5 条目,(1+1)%5=2 → 下轮 45=图数据库
last_run       : 2026-09-22 14:44  # 14:00 槽收尾(开局 14:01:24)
last_top       : 07-数据存储  # 本轮;索引 28 = 02-NoSQL(ID 576-580)
skipped        : []
failed_attempts: []
```

## 三、最近 5 轮(滚动窗口)

| 时间 | 索引 | 主题摘要 | 推进 | 累计 | 来源与质量 | notify |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-09-22 11:29 | 43 | 06-DevOps/07-DevSecOps首批:Semgrep匹配·PURL与VEX·DSSE与P-256·密钥熵·OPA safety重排 | S1+S3 | +5 | 575 | semgrep5+purl+CycloneDX+spdx+dsse2+cosign2+proto3+detect-secrets3+gitleaks4+opa5;Py 261 | notify: ok |
| 2026-09-22 09:03 | 49 | 05-AI与机器学习/08-概率图模型首批:变量消除·连接树·循环BP·HMM·变分推断 | S1+S3 | +5 | 570 | pgmpy×2+nx chordal+ihler05a+SLP3 A+hmmlearn+blei1601+sklearn lda;Py 264 | notify: ok |
| 2026-09-22 06:41 | 18 | 04-移动开发/03-跨平台首批:Yoga布局算法·Dart事件循环与Timer·Hermes值与字节码·Compose快照系统·Tauri IPC与ACL | S1+S3 | +5 | 565 | yoga9+dart4+hermes3+compose2+tauri4;Py 262 | notify: ok |
| 2026-09-22 04:00 | 14 | 03-系统编程/03-内存管理:标记清除与合并·分级分配器对比·ZGC着色指针·perCPU与NUMA·页表与缺页 | S3 | +5 | 560 | dlmalloc+jemalloc.3+tcmalloc+mimalloc+zAddress.hpp+JEP333/439/379+mbind(2)+this_cpu_ops+mm.rst+uffd(2);Py 299 | notify: ok |
| 2026-09-22 14:44 | 28 | 07-数据存储/02-NoSQL第三批:ScyllaDB分片·DynamoDB自适应容量·MongoDB chunk·Cassandra SAI·HBase分裂 | S3 | +5 | 580 | scylladb7+ddb4+mongo2+cs3+hbase3+book;Py211 | notify: ok |

> 更早细节见 `archive/schedule.md`。

## 四、rotate 与查找

- rotate(副任务,不耗主配额):completed.md > 100 / schedule.md > 30 → 截到最近 100/30,丢弃部分查 `git log -p _docs/archive/`
- 某 demo 是否做过 → Grep `completed.md`;上轮资料/坑 → Grep `schedule.md`;总数 → 数 completed.md 行数
- 每轮只动 §三,超 5 KB 时压缩旧行。
