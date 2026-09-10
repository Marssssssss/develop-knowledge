# SEARCH_PROGRESS.md — 知识搜索进度

由自动化任务每 2 小时更新一次（也接受手动编辑）。

> 每轮的**推进量**与**失败回退**规则见 [`SCHEDULE_QUOTA.md`](./SCHEDULE_QUOTA.md)：
> 每轮 **1 主 + ≤ 1 副**（主 = 1 新 demo，副 = 修订/补全/索引 三选一），单 demo = 1 推进单位。

## 一、轮询顺序

按 `priority_score = base_score - done_count * 10 - last_done_days * 0.5` 排序，每次巡检选最高分。

```text
轮询索引表（next_index 字段控制下次从哪个开始）：
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
34 : 09-语言学习/Python     # 按语言组织，与按领域的 01-08 不同
```

## 二、已完成 demo 记录

| ID | 路径 | 知识点 | 语言 | 完成日期 |
| --- | --- | --- | --- | --- |
| 001 | `01-游戏开发/01-服务端/网络编程/IO多路复用/select/` | IO 多路复用 · `select` | C / Python / Go | 2026-09-11 |
| 002 | `09-语言学习/Python/装饰器/` | Python · 装饰器(基础 + 带参数) | Python | 2026-09-11 |
| 003 | `01-游戏开发/01-服务端/网络编程/IO多路复用/epoll/` | IO 多路复用 · `epoll`(LT/ET) | C / Python / Go | 2026-09-11 |
| 004 | `07-数据存储/04-搜索引擎/elasticsearch/` | Elasticsearch · 倒排索引与 NRT(REST API) | Python / Go | 2026-09-11 |
| 005 | `01-游戏开发/02-渲染/图形管线/深度缓冲/` | Z-Buffer 与 Z-Fighting(1/z 深度模型 + 量化精度) | C / Python / Go | 2026-09-11 |
| 006 | `01-游戏开发/03-UI/文本渲染/SDF/` | SDF 文本渲染(有符号距离场 + 双线性重建 + smoothstep AA) | C / Python / Go | 2026-09-11 |
| 007 | `01-游戏开发/04-游戏引擎/ECS/` | ECS 架构(sparse set 存储 + swap-remove + 最小集合查询) | C / Python / Go | 2026-09-11 |
| 008 | `01-游戏开发/05-物理/碰撞检测/GJK/` | GJK 碰撞检测算法(Minkowski 差 + support 函数 + simplex 演化) | C / Python / Go | 2026-09-11 |
| 009 | `01-游戏开发/06-AI/寻路算法/A-star/` | A* 启发式搜索(f=g+h + 可采纳性 + Dijkstra/A*/Greedy 三模式对比) | C / Python / Go | 2026-09-11 |
| 010 | `01-游戏开发/07-音频/音频压缩/IMA-ADPCM/` | IMA ADPCM(自适应差分 + 4-bit 量化 + 步长双查表 + 块随机访问) | C / Python / Go | 2026-09-11 |

## 三、本轮状态

```
next_index     : 7     # 下次巡检从索引 7 开始（即游戏动画领域）
last_run       : 2026-09-11 07:05:00
skipped        : []
failed_attempts: []
```

## 四、调度日志

| 时间 | 索引 | 动作 | 结果 | 备注 |
| --- | --- | --- | --- | --- |
| 2026-09-11 00:30 | 0 | 创建 select demo | OK | C/Python/Go 三语言 |
| 2026-09-11 00:35 | 34 | 新建 09-语言学习 + 装饰器 demo | OK | 按语言组织的新维度,首个 Python demo |
| 2026-09-11 00:45 | — | 制定 SCHEDULE_QUOTA.md | OK | 1 主 + ≤1 副 区间配额;失败重试 3 次后跳过;周 7 / 月 25 demo 下限 |
| 2026-09-11 01:00 | — | 初始化 git + 首次 push + 写 GIT_SYNC.md | OK | main 分支跟踪 origin;`.workbuddy/` 不跟踪;每轮末尾自动 sync |
| 2026-09-11 00:47 | 0 | M=epoll demo(权威资料:epoll(7)/epoll_ctl(2)/Python docs/Go netpoll源码) + S3=索引同步 | OK | C/Python/Go;首个按新"内容来源铁律"产出的 demo |
| 2026-09-11 00:56 | 30 | M=elasticsearch demo(用户指定条目;权威资料:Elastic 官方 index-basics/NRT/match query/Definitive Guide) | OK | Python/Go 标准库 REST 实现;演示倒排索引+text/keyword+NRT refresh |
| 2026-09-11 01:31 | 1 | M=z-buffer demo(权威资料:Khronos OpenGL Wiki Depth Buffer Precision/Marburg 讲义/UBC CPSC414 讲义/Wikipedia) | OK | C/Python/Go 软件光栅化;画家算法 vs Z-Buffer + 16/24-bit Z-Fighting 条带 + 精度表 |
| 2026-09-11 02:31 | 2 | M=SDF 文本渲染 demo(权威资料:Valve SIGGRAPH2007 论文(要点经交叉验证,PDF 镜像均空)/libgdx 官方 wiki 全文/msdfgen 作者 Chlumsky 的 README 与 SE 回答) + S3=索引同步(03-UI 与 01-游戏开发 README 补全) | OK | C/Python/Go;最近邻 vs 二值双线性 vs SDF 双线性三路对比 + 阴影描边特效;新建 文本渲染/ 主题目录 |
| 2026-09-11 03:41 | 3 | M=ECS demo(权威资料:Sander Mertens ECS FAQ 全文/Bevy 0.5 ECS v2 设计文/EnTT README/Unity DOTS 官方页) | OK | C/Python/Go;sparse set + swap-remove + 最小集合查询;archetype/sparse/bitset/reactive 四路线对比;游戏引擎领域首个 demo,next_index → 4 |
| 2026-09-11 04:45 | 4 | M=GJK demo(权威资料:dyn4j GJK 教程全文+评论区勘误/arXiv 2007.12045 算法推导/Inferensys 综述) | OK | C/Python/Go;Minkowski 差 + support + simplex 演化 + Voronoi 区域测试;物理领域首个 demo,next_index → 5 |
| 2026-09-11 05:55 | 5 | M=A* demo(权威资料:Red Blob Games introduction 全文+implementation 章节快照/Wikipedia(IPFS 镜像)/Mastering Algorithms) + S=索引同步(06-AI README) | OK | C/Python/Go;f=g+h+曼哈顿启发式+Dijkstra/A*/Greedy 三模式同图对比(最优代价 39 vs Greedy 45);游戏 AI 领域首个 demo,next_index → 6 |
| 2026-09-11 07:05 | 6 | M=IMA ADPCM demo(权威资料:Digital Technical Journal Vol.5 No.2 Pan93 论文全文(经 PDF 文本提取实际阅读)/RFC 3551 §4.5.1 全文) | OK | C/Python/Go;流式 4:1 编解码+nibble 篡改抗误码演示+块头随机访问解码;游戏音频领域首个 demo,新建 音频压缩/ 主题目录;发现论文步长表索引 84(22358)与标准实现(22385)分歧,已注明;next_index → 7 |

---

> 自动化任务在每次结束后追加一行到此表。