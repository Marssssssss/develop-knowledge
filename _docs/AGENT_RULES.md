# AGENT_RULES.md — Agent 行为规范

Agent（包括自动化巡检、手动对话）在 `D:\开发研究\` 下操作时必须遵守的规则。

## 一、目录写入规则

1. **只动本项目**：不要在 `D:\开发研究\` 之外写文件，除非用户明示。
2. **每个新知识点必须落到对应目录**：
   - 顶层选 `01-游戏开发` / `02-Web开发` / ... / `08-安全` / `09-语言学习` / `10-逆向工程` / `11-性能分析` 中最贴切的一个。
   - 二级选最贴切的细分（如游戏开发下分服务端 / 渲染 / ...）。
   - 三级及以下按需新建。
3. **新建目录必须同时写 README**：README 内容见 [DEMO_TEMPLATE.md](./DEMO_TEMPLATE.md) 第 1 节。
4. **不动旧 demo**：除非用户明示，不要修改已完成的 demo 内容（防止破坏阅读连贯性）。
5. **类目自动拓展**（2026-09-12 新增，自动化巡检与人工操作均适用）：
   - 同一父类目下，子类目须覆盖该维度主流方向（如 `09-语言学习` 不能只有 Python，应补 Golang/Rust/JS/Java/C/C++/C#/SQL/Shell 等；发现缺口即补，不等用户指定）。
   - 新建子类目 = 建目录 + 写 README（简介 + 知识点"待研究"清单，遵守内容来源铁律，**不允许空壳**）+ 追加到 [`STATE.md`](./STATE.md) 第一节轮询索引表末尾（编号顺延）。
   - 自动化巡检每轮新增子类目 ≤ 2 个，计入副任务配额；顶层 `01-`~`09-` 一级目录不自动新增。

## 二、工具调用规则

1. **优先专用工具**：不要用 Bash 跑 `cat` / `ls` / `grep`；用 Read / Glob / Grep。
2. **并行独立调用**：多个独立操作（如读多个文件）应放在同一个回合里并发执行。
3. **不重复读取**：已读过的文件不要再次 Read；用 Edit 直接修改。
4. **长任务后台化**：构建、安装、爬虫等用 `run_in_background=true`，然后监听 `<task-notification>`。
5. **绝不删除 `D:\开发研究\.workbuddy\`**：这是项目级 memory 与 skill 存储。

## 三、搜索/调研规则

1. **优先权威源**：官方文档 > 大厂博客 > 知名个人博客 > Stack Overflow > AI 生成。
2. **先广后深**：先看 1-2 篇综述/官方，再深入细节。
3. **结果量化**：能用代码验证的，必须给最小 demo；纯概念性知识也要给参考链接。
4. **去重**：发现的新知识点与现有 demo 重合时，**补充到现有 demo** 而不是新建。

## 四、自动化执行规则

由 `automation_update` 创建的每 **2 小时**一次的任务(单条 `FREQ=HOURLY;INTERVAL=2` RRULE,详见 `SCHEDULE_QUOTA.md` 顶部说明;全天 12 个触发点 `00:00 02:00 ... 22:00`):

1. **轮询领域**:按 [`STATE.md`](./STATE.md) 第二节"本轮状态"中的 `next_index` 选下一个待研究领域(状态文件 ≤ 5 KB,默认 always-read 即可)。
2. **单次最多产出 5 个新 demo**:与 `SCHEDULE_QUOTA.md`「5 主/轮」配额对齐;叠加副任务单轮 ≤ 7 动作。
3. **不主动执行 demo**:README 里只写"如何运行",Agent 不实际跑代码。
4. **失败重试上限 2 次**:连续失败则跳过本轮,记入 STATE.md 的 `failed_attempts` 字段(见 STATE.md §2 + §4)。
5. **进度日志**:每轮结束
   - 在 `STATE.md` 第三节"最近 5 轮"中**滚动追加**本轮摘要
   - 在 `archive/completed.md` append 一行(每 demo 完成后)
   - 在 `archive/schedule.md` append 一行(每轮末尾)
   - 在 `.workbuddy/memory/YYYY-MM-DD.md` append 一行(当日累计)

## 五、与其他规则文档的关系

- Skill 安装/删除:[SKILL_RULES.md](./SKILL_RULES.md)
- 存储与 Agent 消耗优化:[OPTIMIZATION.md](./OPTIMIZATION.md)(§2.4 专门讲状态文件瘦身)
- demo 模板:[DEMO_TEMPLATE.md](./DEMO_TEMPLATE.md)
- 状态文件(tracking):[`STATE.md`](./STATE.md)(主,≤ 5 KB)+ [`archive/`](./archive/)(历史,按需 Grep)