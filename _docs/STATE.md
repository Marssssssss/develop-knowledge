# STATE.md — 自动化巡检主状态文件

> 每轮结束由自动化任务追加/滚动。**永远 ≤ 5 KB,不再随 demo 数线性增长**。
> 历史全本见 `_docs/archive/`(默认不进 hot path,按需 Grep)。

> 推进配额与失败回退:见 [`SCHEDULE_QUOTA.md`](./SCHEDULE_QUOTA.md)
> Token 控制细节:见 [`OPTIMIZATION.md` §2.4](./OPTIMIZATION.md)

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
```

> **索引表可动态增长**（2026-09-12 起）：巡检副任务自动拓展子类目时，在表末尾顺延编号追加（35、36…），`next_index` 取模基数以实际行数为准。类目拓展规则见 `AGENT_RULES.md` §一.5。

## 二、本轮状态

```
next_index     : 21    # 下次从索引 21 开始(即 05-AI与机器学习/LLM)
last_run       : 2026-09-12 00:57
skipped        : []
failed_attempts: []
```

## 三、最近 5 轮(滚动窗口)

| 时间 | 索引 | 主题 | demo |
| --- | --- | --- | --- |
| 2026-09-12 00:57 | 20 | Q-Learning 表格方法 + REINFORCE 策略梯度 + PPO 近端策略优化 三件套 | +3 |
| 2026-09-11 23:34 | 19 | 反向传播 BP + 2D 卷积 im2col + 多头自注意力 三件套 | +3 |
| 2026-09-11 22:30 | 18 | React Native Bridge vs JSI + Flutter 三棵树 + KMP expect/actual 三件套 | +3 |
| 2026-09-11 21:22 | 17 | Android Handler/Looper + Activity launchMode + Compose 重组 三件套 | +3 |
| 2026-09-11 20:16 | 16 | Swift/ObjC ARC + GCD 同步原语 + RunLoop 三件套 | +3 |

> 超出 5 轮的细节见 `_docs/archive/schedule.md`(完整日志)+ `git log -p`(历史回溯)。

## 四、归档与 rotate 规则

| 文件 | 内容 | rotate 触发 | 截断阈值 | 超出处理 |
| --- | --- | --- | --- | --- |
| `_docs/archive/completed.md` | 已完成 demo 全本 | 行数 > 100 或 大小 > 50 KB | 保留最近 100 行 | 直接丢弃(查 `git log`) |
| `_docs/archive/schedule.md` | 调度日志全本 | 行数 > 30 或 大小 > 20 KB | 保留最近 30 条 | 直接丢弃(查 `git log`) |

- rotate 作为**副任务**(修订/补全/索引 三选一)执行,不消耗主任务配额
- 截断部分**直接丢弃**,不另存冷归档 — 历史回溯靠 `git log -p _docs/archive/`
- 写入新数据时:本文件**只动第三节**(滚动窗口),archive 文件 append 即可

## 五、查找路径

| 想知道什么 | 看哪里 |
| --- | --- |
| 下次从哪个领域开始 | 第二节"本轮状态" |
| 最近 5 轮做了什么 | 第三节"滚动窗口" |
| 某 demo 是否做过 | Grep `_docs/archive/completed.md` `001-999`,或 `git log --all --oneline -- <demo-path>` |
| 上轮日志详细(权威资料/坑/代码量等) | Grep `_docs/archive/schedule.md`,或 `git log -p _docs/archive/` |
| 历史 demo 列表的总数 | 数 completed.md 行数(接近 100 时该 rotate) |

---

> 本文件由 `automation_update` 注册的巡检任务每轮末尾追加;人工编辑直接 Edit 即可,大改请同步更新 `_docs/OPTIMIZATION.md §2.4`。
