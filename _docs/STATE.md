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
57    10-逆向工程: 固件与嵌入式逆向
58    02-Web开发: WebAssembly
```

> 39-58 为类目自动拓展新增(S2),新子类目顺延 59 起;条目数 9/8/4/4/9/8/5/4/5/4/3 = 59。

## 二、本轮状态

```
top_pos        : 10        # 下轮取循环[10]=11-性能分析
sub_pos        : 01:4 02:1 03:1 04:1 05:6 06:5 07:0 08:2 09:1 10:2 11:1  # 消费 sub_pos[10]=1 → 索引 36 移动端逆向
last_run       : 2026-09-20 22:40  # 22:00 槽收尾(开局 22:00:45)
last_top       : 10-逆向工程  # 本轮消费循环[9] → 索引 36 移动端逆向
skipped        : []
failed_attempts: []
```

## 三、最近 5 轮(滚动窗口)

| 时间 | 索引 | 主题摘要 | 推进 | 累计 | 来源与质量 | notify |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-09-20 22:40 | 36 | 10-逆向工程/移动端逆向第二批:DEX格式·Dalvik指令编码·FridaStalker·iOS砸壳·反调试与越狱对抗 | S1+S2+S3 | +5 | 486 | AOSP dex-format/dalvik-bytecode/instruction-formats 三篇+frida JS API+gumevent.h+xnu loader.h+MASTG 0046/0354/0240/0241 直抓;Py 606 断言 | notify: ok |
| 2026-09-20 20:39 | 34 | 09-语言学习/Python第四批:迭代器·MRO与C3·参数绑定·运算符分派·作用域闭包 | S3 | +5 | 481 | datamodel+itertools+functools+PEP570/479+typeobject.c 直抓;Py 43 组 | notify: ok |
| 2026-09-20 18:00 | 32 | 08-安全/Web安全第三批:DPoP·TrustedTypes·strict-dynamic·SRI·COOP-COEP-CORP | S1+S3 | +5 | 476 | RFC9449/8705+CSP3+SRI+COEP+HTML 直抓;Py 230 断言 | notify: ok |
| 2026-09-20 16:50 | 45 | 07-数据存储/图数据库第三批:路径匹配·null三值·索引·约束·Louvain | S3 | +5 | 471 | neo4j.com+arXiv 0803.0476 直抓;Py 334 断言 | notify: ok |
| 2026-09-20 14:46 | 41 | 06-DevOps/IaC第四批:重构块·lifecycle·插件协议·Ansible·unknown | S3 | +5 | 466 | HashiCorp/Ansible+objchange.go 直抓;Py 162 断言 | notify: ok |

> 更早细节见 `archive/schedule.md`。

## 四、rotate 与查找

- rotate(副任务,不耗主配额):completed.md > 100 行 / schedule.md > 30 行 → 截到最近 100/30,丢弃部分查 `git log -p _docs/archive/`
- 某 demo 是否做过 → Grep `completed.md`;上轮资料/坑 → Grep `schedule.md`;总数 → 数 completed.md 行数
- 每轮只动 §三且每行 ≤120 字,超 5 KB 时同步压缩旧行;大改同步 `OPTIMIZATION.md §2.4`。
