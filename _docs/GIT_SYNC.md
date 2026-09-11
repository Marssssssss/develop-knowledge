# GIT_SYNC.md — 更改后的 Git 同步规范

> 规则:**每次更改后都要立即同步**。无论是定时巡检产生的改动,还是人工对话中 Agent 对仓库的任何修改,完成后马上 commit + push,不等下一轮巡检。

## 一、仓库与远端

| 项 | 值 |
| --- | --- |
| 本地路径 | `D:\开发研究\` |
| 远端 | `git@github.com:Marssssssss/develop-knowledge.git` |
| 分支 | `main` |
| 远端 owner | `Marssssssss` |
| 提交身份 | `Marssssssss <Marssssssss@users.noreply.github.com>`(本仓库本地配置,不污染全局) |
| SSH | 已验证可用(`Hi Marssssssss! You've successfully authenticated`) |

## 二、何时触发

**两类触发,均为"改完即同步"**(2026-09-12 升级):

| 场景 | 触发时机 |
| --- | --- |
| 定时巡检 | 每轮巡检内**每个产生改动的步骤完成后**即可提交一次;至少保证**轮末必须同步**(在「索引同步」副任务之后;当前 1 小时/轮) |
| 人工对话 / 手动修改 | Agent 对 `D:\开发研究\` 的任何修改(规则文档、类目拓展、demo 等)完成后**立即**走本流程,**不等下一轮巡检** |

执行条件:

```bash
git status --porcelain | grep -q .
```

- 有改动 → 走「自动 commit + push」流程(见第三节)
- 无改动 → 跳过,**不创建空 commit**

## 三、自动 commit + push 流程

```bash
# 1. 检查
cd "D:/开发研究"
if [ -z "$(git status --porcelain)" ]; then
  echo "no changes, skip"
  exit 0
fi

# 2. 先 pull,再 push(避免远端先被人改动)
git pull --rebase origin main 2>&1
# 若 rebase 失败 → 跳到「冲突处理」(第五节)

# 3. add + commit
git add -A
git commit -m "auto: $(date +'%Y-%m-%d %H:%M') - <简述>"

# 4. push
git push origin main
```

**commit 消息格式**:

```text
auto: YYYY-MM-DD HH:MM - <简要描述>

例:
auto: 2026-09-11 02:00 - +1 demo: 03-系统编程/epoll
auto: 2026-09-11 04:00 - S3: 索引同步
auto: 2026-09-11 06:00 - S1: select demo 补 Rust 实现
```

格式约定:

- 前缀 `auto:` 区分自动化提交与人工提交
- 时间戳用本地时间(GMT+8)
- 简述 40 字以内,涉及 demo 写 `+N demo: 路径`,涉及副任务写 `S1/S2/S3: 简述`

## 四、`.gitignore`(不推的内容)

| 路径/模式 | 原因 |
| --- | --- |
| `.workbuddy/` | Agent 内部状态(MEMORY.md、daily log、automation 等),不属于知识本体 |
| OS/IDE 临时文件 | `Thumbs.db`、`Desktop.ini`、`.DS_Store`、`.vscode/`、`.idea/`、`*.swp` 等 |
| 编译产物 | `__pycache__/`、`*.pyc`、`target/`、`build/`、`dist/`、`node_modules/` |
| Python 虚拟环境 | `.venv/`、`venv/`、`env/` |

> `.workbuddy/` 不进公共仓库,既因为含 Agent 内部状态,也因为避免把 automation id 等元数据外泄。
> 如果用户希望保留 MEMORY.md 的版本历史,可改为**单独拉一个私有仓库**或只推送 MEMORY.md 文件。

## 五、冲突与失败处理

| 场景 | 处理 |
| --- | --- |
| `pull --rebase` 成功 | 继续 add + commit + push |
| `pull --rebase` 冲突 | **终止本次同步**,在 `archive/schedule.md` 加一行 `conflict: 需要人工解决`,然后走失败回退(见 SCHEDULE_QUOTA.md 第四节) |
| `push` 失败(网络/权限) | 记录 `push_failed: <原因>`,**不阻塞**下一轮巡检;下一轮再次尝试 |
| 连续 3 轮 push 失败 | 在调度日志告警 `git sync: 连续 3 轮失败`,同时**暂停自动推送**,等用户介入 |

**原则**:**宁可跳过,不可静默吞掉失败**;**宁可告警,不可让失败无限重试**。

## 六、禁止动作

1. **不要 `git push --force`** —— 远端可能被其他设备/流程推送过,强推会覆盖
2. **不要 commit `.workbuddy/`** —— 已 `.gitignore` 保护,但**禁止**用 `git add -f` 强制加
3. **不要覆盖本仓库 git 全局配置** —— user.name/email 只在 `--local` 范围配置
4. **不要自动 merge 冲突文件** —— 冲突走告警 + 人工解决,避免语义冲突被自动「吞掉」
5. **不要在外部目录执行本仓库的 git 命令** —— 所有 git 操作 `cd "D:/开发研究"` 后再执行

## 七、首次初始化(2026-09-11 已完成)

```bash
cd "D:/开发研究"
git init -b main
git config user.name "Marssssssss"
git config user.email "Marssssssss@users.noreply.github.com"
git config core.quotepath false       # 让 git status/log 正确显示中文路径
git remote add origin git@github.com:Marssssssss/develop-knowledge.git
git add .
git commit -m "init: 开发知识库 + 9 大领域目录 + 首个 demo (select) + 09-语言学习/Python 装饰器"
git push -u origin main
```

## 八、变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-09-11 | 首版定义,同时完成首次 push |
| 2026-09-11 | 调度频率由 2 小时/轮 → 1 小时/轮(与 SCHEDULE_QUOTA 配额上调 3 主+≤2 副 同步) |
| 2026-09-12 | 触发时机升级:「每轮巡检末尾同步」→「**每次更改后立即同步**」,人工对话中的修改同样适用 |
