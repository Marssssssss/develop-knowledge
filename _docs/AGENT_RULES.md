# AGENT_RULES.md — Agent 行为规范

Agent（包括自动化巡检、手动对话）在 `D:\开发研究\` 下操作时必须遵守的规则。

## 一、目录写入规则

1. **只动本项目**：不要在 `D:\开发研究\` 之外写文件，除非用户明示。
2. **每个新知识点必须落到对应目录**：
   - 顶层选 `01-游戏开发` / `02-Web开发` / ... / `08-安全` 中最贴切的一个。
   - 二级选最贴切的细分（如游戏开发下分服务端 / 渲染 / ...）。
   - 三级及以下按需新建。
3. **新建目录必须同时写 README**：README 内容见 [DEMO_TEMPLATE.md](./DEMO_TEMPLATE.md) 第 1 节。
4. **不动旧 demo**：除非用户明示，不要修改已完成的 demo 内容（防止破坏阅读连贯性）。

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

由 `automation_update` 创建的每 2 小时一次的任务：

1. **轮询领域**：按 `_docs/SEARCH_PROGRESS.md` 中的 `next_index` 选下一个待研究领域。
2. **单次最多产出 3 个新 demo**:与 `SCHEDULE_QUOTA.md`「3 主/轮」配额对齐;叠加副任务单轮 ≤ 5 动作。
3. **不主动执行 demo**：README 里只写"如何运行"，Agent 不实际跑代码。
4. **失败重试上限 2 次**：连续失败则跳过本轮，记入 SEARCH_PROGRESS 的 `skipped` 列。
5. **进度日志**：每轮结束追加到 `.workbuddy/memory/YYYY-MM-DD.md` 与本文件。

## 五、与其他规则文档的关系

- Skill 安装/删除：[SKILL_RULES.md](./SKILL_RULES.md)
- 存储与 Agent 消耗优化：[OPTIMIZATION.md](./OPTIMIZATION.md)
- demo 模板：[DEMO_TEMPLATE.md](./DEMO_TEMPLATE.md)
- 搜索进度追踪：[SEARCH_PROGRESS.md](./SEARCH_PROGRESS.md)