# OPTIMIZATION.md — 存储与 Agent 消耗优化

本文件控制"不爆仓"和"不烧 token"两条底线。

## 一、存储优化

### 1.1 单 demo 体量上限

| 文件类型 | 上限 | 说明 |
| --- | --- | --- |
| 单源代码文件 | ≤ 300 行 | 超出则考虑拆分 |
| README.md | ≤ 200 行 | 超长拆多页 + 索引 |
| 单语言实现总大小 | ≤ 50 KB | 含依赖锁文件也计数 |

### 1.2 不应保存的产物

- `node_modules/`、`venv/`、`target/`、`build/`、`dist/` 等构建产物 → 一律不提交本目录
- 大型模型权重、二进制 fixture → 存外链或 `.gitignore`
- 自动生成的 lock 文件（`package-lock.json` / `Cargo.lock` 除外，保留以便复现）

### 1.3 目录体量预估

按 8 顶层 × 6 二级 × 5 三级 = 240 个目录，每个目录平均 ~30 KB（代码 + README），总预算 **~7 MB**。

若接近上限，启动"瘦身"流程：
1. 合并相似 demo
2. 把完成度低/无关的删除
3. 把过时的归档到 `_docs/archive/`

## 二、Agent 消耗优化

### 2.1 单轮 Token 控制

- **目标**：单轮 ≤ 30K 输入 tokens（不计工具结果）
- **超出判断**：超过 50K 应主动放弃深读，转用 WebSearch/WebFetch 摘要
- **复用上下文**：不重复 Read 已读过的文件

### 2.2 搜索策略

| 场景 | 用什么工具 |
| --- | --- |
| 已知 URL | `WebFetch` 直读 |
| 找权威综述/官方 | `WebSearch` 限定 `allowed_domains` |
| 复杂多源交叉研究 | `agentic_search`（昂贵） |

### 2.3 自动化任务的 Agent 配置

由 `automation_update` 创建的巡检任务,prompt 中显式约束:
- 不展开调研综述类问题
- 单轮最多调用 3 次 `agentic_search`(与 SCHEDULE_QUOTA「3 主/轮」配额对齐)
- 单轮最多调用 9 次 `WebSearch` + 6 次 `WebFetch`(与「3 主 + ≤2 副」总动作对齐)

### 2.4 状态文件瘦身(每轮 Read 的成本控制)

自动化任务**起手只 Read [`STATE.md`](./STATE.md)**(≤ 5 KB 永久恒定)即可定位当轮要做什么。历史 detail 默认不读,需查时按需 Grep `_docs/archive/`。

**文件职责**(拆分原 `SEARCH_PROGRESS.md` 于 2026-09-11):

| 文件 | 大小 | 角色 | 写入节奏 |
| --- | --- | --- | --- |
| [`_docs/STATE.md`](./STATE.md) | **≤ 5 KB,永久恒定** | 轮询顺序 + 本轮状态 + 最近 5 轮 + 归档指针 | 每轮只动第三节"滚动窗口" |
| `_docs/archive/completed.md` | rotate 阈值 100 行 / 50 KB | 已完成 demo 全本(append-only) | 每 demo append 1 行 |
| `_docs/archive/schedule.md` | rotate 阈值 30 行 / 20 KB | 调度日志全本(append-only) | 每轮 append 1 行 |

**Rotate 规则**(任一条件触发):
- `completed.md` 行数 > 100 **或** 大小 > 50 KB → 截断至最近 100 行
- `schedule.md` 行数 > 30 **或** 大小 > 20 KB → 截断至最近 30 条
- 超出的部分**直接丢弃**(不另存冷归档),历史回溯靠 `git log -p _docs/archive/`
- rotate 动作视为**副任务**(修订/补全/索引 三选一),不消耗主任务配额

**Token 收益**(对比拆分前后):
- 拆分前:每轮 Read `SEARCH_PROGRESS.md` 全量 15.9 KB ≈ 4.4K tokens
- 拆分后:每轮 Read `STATE.md` 3.8 KB ≈ 1.0K tokens
- 单轮起手成本 **省 76%**,且**永久**不再随 demo 数线性增长
- 预估:1 周后(≈ 100 个 demo)状态文件仍是 ≤ 5 KB,而旧方案已 ~70 KB

## 三、提示词与记忆优化

- **项目记忆**写 `D:\开发研究\.workbuddy\memory/`（daily log + MEMORY.md）
- **跨项目偏好**写 `~/.workbuddy/MEMORY.md`
- 避免在对话中重复长上下文；用 Read 工具按需取。

## 四、巡检任务自检清单

每次巡检结束前检查：

- [ ] 没有未写入 README 的代码
- [ ] 没有遗漏更新 `_docs/STATE.md`(第三节窗口) + 追加到 `_docs/archive/{completed,schedule}.md`
- [ ] 当天 `.workbuddy/memory/YYYY-MM-DD.md` 已追加一行
- [ ] 总目录数 / 总大小未超预算
- [ ] 没有引入新的 npm/Gradle/Maven 依赖未记录