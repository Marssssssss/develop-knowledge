# 定时任务备份与恢复说明（2026-09-25 停管归档）

> 2026-09-25 用户指示：`D:\开发研究\` 项目不再由 AI 管理，全部定时任务已删除。
> 本目录是删除前的**完整备份**，恢复步骤见下。流程权威文件 `_docs/AUTOMATION_PROMPT.md` 未删，仍在仓库内。

## 一、备份内容

- `automations_backup.json`：从 `~/.workbuddy/workbuddy.db`（表 `automations`）导出的**全字段**快照
  - `live_automations`：12 条现役「开发研究-自动巡检」定点任务（00/02/04/06/08/10/12/14/16/18/20/22 时整点）
  - `legacy_deleted_refs`：2 条更早的历史任务残留引用（旧 2h 单条 `cadf31f5…`、B 整点错位 `4fc1d95d…`，停管前即已删除，仅作查档）
- 每条记录含：id、name、**prompt 全文**、rrule、schedule_type、status、cwds、model_id（= `balanced-model`）、valid_from/until、时间戳等全部列。

## 二、停机时项目状态（恢复时对账用）

```
停管时间 : 2026-09-25 01:3x
last_run : 2026-09-25 00:42（08-安全/01-Web安全 第四批，+5，累计 707 demo，notify: ok）
top_pos  : 8   # 下轮应消费 [8]=09-语言学习
sub_pos  : 01:0 02:5 03:1 04:1 05:1 06:1 07:4 08:2 09:0 10:1 11:5 12:1
```

## 三、恢复步骤

1. 打开 `automations_backup.json`，取 `live_automations` 中任一条的 `prompt`（12 条仅"槽位时间"一处不同，恢复时把 prompt 中 `**HH:00 定点槽**`、`触发时刻应为 **HH:00**` 两处替换为目标小时即可）。
2. 用 automation 工具逐条重建，共 12 条，参数：
   - `name`: `开发研究-自动巡检 HH:00`
   - `scheduleType`: `recurring`
   - `rrule`: `FREQ=DAILY;INTERVAL=1;BYHOUR=<H>;BYMINUTE=0`（H ∈ 0,2,4,…,22）
   - `status`: `ACTIVE`
   - `cwds`: `D:\开发研究`
   - 模型：传 `model: "balanced-model"`（db 字段 model_id；view/list 不回显，须查 db 核验）
3. 重建后核对：`automation list` 应出现 12 条，且每条 `nextRunAt` 为下一个对应整点。
4. 恢复前先读 `_docs/STATE.md` 与 `_docs/AUTOMATION_PROMPT.md`，确认指针状态未被他人推进。

## 四、避坑（重建时必读）

- **禁用 `FREQ=HOURLY`**：nextRunAt = 上轮 finishedAt + INTERVAL，会逐轮漂移；整点必须用 `DAILY;BYHOUR=单值`（12 条定点任务）。
- `BYHOUR` **不能写逗号列表**（`BYHOUR=2,4` 会静默变成 2）。
- 轮间保险阈值 45 分钟；时间戳一律 shell `date` 实取，禁推算。
- 恢复动作本身要避开整点前 5 分钟窗口（一次性/新任务排程需几分钟提前量）。
- 新任务 ID 必然与备份中不同；旧 ID 仅作查档。

## 五、相关文件

- 流程定义（未删）：`_docs/AUTOMATION_PROMPT.md`、`_docs/SCHEDULE_QUOTA.md`、`_docs/AGENT_RULES.md`
- 状态快照（未删，停机时点见上）：`_docs/STATE.md`
- 企业微信 webhook（未删）：`_docs/.wecom_webhook`
