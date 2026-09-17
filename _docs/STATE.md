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
```

> 39-55 为类目自动拓展新增(S2);新子类目顺延 56 起。条目数 01:8 02:4 03:4 04:4 05:9 06:8 07:5 08:4 09:4 10:3 11:3 = 56。

## 二、本轮状态

```
top_pos        : 9        # 下一轮取循环第 9 位 → 10-逆向工程(本轮已取第 8 位 = 09-语言学习)
sub_pos        : 01:1 02:2 03:2 04:2 05:3 06:2 07:2 08:2 09:2 10:1 11:1  # 各大类内子类目指针
last_run       : 2026-09-18 02:00  # 02:00 槽占位锁(shell date 实测);保险 45 分钟
last_top       : 09-语言学习  # 信息字段,不参与决策
skipped        : []
failed_attempts: []
```

## 三、最近 5 轮(滚动窗口)

| 时间 | 索引 | 主题摘要 | 推进 | 累计 |
| --- | --- | --- | --- | --- |
| 2026-09-18 00:29 | 44 | Golang 第三批 5 demo:内存分配器(68 size class/tiny/三级缓存)、逃逸分析与内联(derefs 权重/预算表)、内存模型与 sync 原语(HB 闭包/Mutex 双模式)、反射三定律(flag 位/CanSet 边界)、unsafe 与内存布局(pointer bytes/六种模式);Py 372 断言全绿 | S1+S3 rotate(ID 212..311) | +5 | 311 | 7 WebFetch+13 curl;槽位 ✓ 00:00 |
| 2026-09-17 23:03 | 32 | Web安全第二批:OAuth2 PKCE、Cookie/HSTS、SSRF+WHATWG IPv4、访问控制 IDOR、点击劫持 XFO/CSP;Py 273+JS 105 断言 | S1+S3 rotate(ID 207..306) | +5 | 306 | 9 WebFetch+5 curl;槽位 ✗ 22:47(20:00 补跑) |
| 2026-09-17 14:36 | 28 | NoSQL 第二批:MongoDB ESR、DynamoDB 台阶、Cassandra 墓碑、LSM WAL、Protobuf;351 断言 | S1+S3 rotate(ID 197..301) | +5 | 301 | 8 WebFetch+1 WebSearch;槽位 ✓ |
| 2026-09-17 12:34 | 24 | CI-CD 第二批:Actions 过滤器/matrix、GitLab rules、Jenkins post、Argo CD 三态、SemVer+OCI;431 断言 | S1+S3 rotate(ID 197..296) | +5 | 296 | 10 WebFetch;槽位 ✓ |
| 2026-09-17 11:14 | 20 | 强化学习第二批:DQN 回放+目标网络、Double Q、TD(λ)、Actor-Critic+GAE、SAC;68 断言 | +5 | 291 | 全 Python |

> 更早细节见 `_docs/archive/schedule.md`(完整日志)+ `git log -p`(历史回溯)。

## 四、rotate 与查找

- rotate(副任务,不耗主配额):completed.md > 100 行 / schedule.md > 30 行 → 截到最近 100/30,丢弃部分查 `git log -p _docs/archive/`;本文件只动 §三,archive append 即可
- 某 demo 是否做过 → Grep `completed.md`;上轮资料/坑 → Grep `schedule.md`;总数 → 数 completed.md 行数
- 每轮只动 §三且每行 ≤120 字,超 5 KB 时同步压缩旧行;大改同步 `OPTIMIZATION.md §2.4`。
