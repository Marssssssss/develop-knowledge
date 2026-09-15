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
50: 09-语言学习/Rust  51: 06-DevOps/平台工程
52: 05-AI与机器学习/RAG与向量检索
53: 09-语言学习/TypeScript  54: 08-安全/应用安全与供应链
```

> 39-54 为类目自动拓展新增(2026-09-12 起 S2);表尾编号即行数,新子类目顺延 55 起。
> 取模基数按实际行数(`AGENT_RULES` §一.5);大类轮换:顶层大类须 ≠ 上轮 `last_top`,同大类顺延不记 skip;`next_index` = 选中索引+1。

## 二、本轮状态

```
next_index     : 24
last_run       : 2026-09-16 00:10
last_top       : 06-DevOps
skipped        : []
failed_attempts: []
```

## 三、最近 5 轮(滚动窗口)

| 时间 | 索引 | 主题摘要 | 推进 | 累计 |
| --- | --- | --- | --- | --- |
| 2026-09-16 00:10 | 23 | 容器化第二批 5 个:Docker 层缓存(链式键、COPY 元数据校验和且 mtime 除外、RUN 只看命令串;好行序 4/2 vs 坏行序 2/3)、多阶段构建(814MB→14MB、BuildKit 只建依赖阶段 3 vs legacy 4、mode=max 导中间层)、UID 映射(只能写一次、340 行与一页互挤、overflow 65534、subuid 231072:65536)、rootless(subuid≥65536、驱动白名单、cgroup 默认只委派 memory+pids 致 --cpus 静默失效)、cgroup+eBPF(subtree_control 四规则、no internal process、BPF token 四项委派+ns_capable);157 断言全绿 | +5 | 241 |
| 2026-09-15 22:22 | 19 | 深度学习第二批 5 个:LayerNorm/RMSNorm(per-sample 统计量、Pre/Post-LN 梯度比 3.58 vs 0.67)、Softmax+CE(∂ℓ/∂x=p−y、mean 除数是 Σw)、残差连接(退化复现、首末梯度比 93.9→3.3)、学习率调度(公式 3 相交、SGDR 0/10/30/70)、混合精度(2^-25 归零 20.75%、FP16 权重 20000 步不动);131 断言全绿 | +5 | 236 |
| 2026-09-15 20:30 | 16 | iOS 第三批:Cassowary 求解、SwiftUI Observation、Swift 并发(job 树/actor 可重入)、dyld pre-main、CA 渲染管线;119 断言全绿 | +5 | 231 |
| 2026-09-15 18:26 | 12 | 网络编程 5 个:TCP 拥塞控制(Reno/CUBIC)、零拷贝、backlog/SYN 队列、scatter-gather、SCM_RIGHTS;76 断言全绿;S1=拆 15 处超限文件 | +5 | 226 |
| 2026-09-15 16:22 | 8 | 前端框架 5 个:React Concurrent(Lane/切片)、Vue 编译器优化、Signals、SSR-Hydration、Turbopack(读时依赖/内容相等短路);186 断言全绿;S1+S2 类目+2 | +5 | 221 |

> 更早细节见 `_docs/archive/schedule.md`(完整日志)+ `git log -p`(历史回溯)。

## 四、rotate 与查找

- rotate(副任务执行,不耗主配额):completed.md > 100 行 / schedule.md > 30 行 → 截到最近 100/30 行,丢弃部分查 `git log -p _docs/archive/`;写新数据时本文件只动 §三,archive append 即可
- 某 demo 是否做过 → Grep `completed.md`;上轮权威资料/坑 → Grep `schedule.md`;总数 → 数 completed.md 行数
- 本文件每轮末尾追加;§三 每行压到 ≤120 字并同步压缩旧行以守住 ≤5 KB;大改同步 `OPTIMIZATION.md §2.4`。
