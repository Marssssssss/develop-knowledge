# DAG 流水线调度（Kahn 拓扑排序 + needs 依赖语义）

## 简介

现代 CI 系统（GitLab CI 的 `needs`、GitHub Actions 的 `jobs.<id>.needs`）都把流水线建模为
**有向无环图（DAG）**：job 之间声明"我依赖谁"，调度器只等真正的前置 job 完成，而不是等整个
stage 结束。本 demo 用 Kahn 算法从零实现一个最小 DAG 流水线引擎，覆盖四件事：

- **拓扑分层**：入度计数 + 队列，逐层输出可并行执行的 job 集合
- **环检测**：`needs` 写成环时流水线创建直接失败（拓扑结果数 < 节点数即有环）
- **关键路径**：`earliest_finish[j] = dur[j] + max(earliest_finish[need])`，即无限并发下的
  流水线最短 wall-clock 时间
- **失败传播**：某 job 失败后，所有（直接或间接）依赖它的 job 被标记为 SKIPPED

关键概念：

| 概念 | 一句话解释 |
| --- | --- |
| stage barrier（阶段屏障） | 传统流水线：前一 stage 全部完成，下一 stage 才开始 |
| needs / DAG 调度 | job 声明依赖，前置一完成立即启动，打破 stage 屏障 |
| Kahn 算法 | 反复取入度 0 的节点出队，删其出边，O(V+E) |
| 关键路径 | DAG 上耗时最长的依赖链，决定流水线下限时长 |

历史背景：GitLab 2019 年引入 `needs` 关键字实现 DAG 流水线（官方博客给出经典算例：build A
1 分钟、build B 5 分钟、test C 只依赖 A —— stage 屏障下 C 要空等 4 分钟，DAG 下立即启动）。

## 原理详解

### Kahn 拓扑排序（分层版）

```
1. 统计每个 job 的入度 = len(needs)（依赖数）
2. 入度为 0 的 job 全部入队 → 这是第 0 层（可立即并行启动）
3. 取出队首 job j，对每个后继 s：入度减 1；减到 0 则入队
4. 每清空一轮队列记为一层（同一层的 job 可并行）
5. 出队总数 < 节点总数 ⇒ 图中有环，流水线创建失败
```

```
菱形依赖(GitLab 官方文档算例):

      build ──┬──> test_unit ──┐
              ├──> test_intg ──┼──> deploy
              └──> test_perf ──┘

拓扑分层:  [build]  ->  [test_unit, test_intg, test_perf]  ->  [deploy]
```

### 关键路径（流水线最短 wall-clock）

对拓扑序中的每个 job：

```
earliest_finish[j] = duration[j] + max(earliest_finish[n] for n in needs[j])
                    （无依赖时 max 取 0）
```

整条流水线的最短时长 = `max(earliest_finish)`，回溯 max 来源即得关键路径。

### stage 屏障 vs DAG

| 调度方式 | 总时长公式 |
| --- | --- |
| stage barrier | `sum over stages( max duration in stage )` |
| DAG（无限并发） | `max over jobs( earliest_finish )` = 关键路径 |

### 失败传播（GitHub Actions 语义）

官方文档原话：*"If a job fails, all jobs that need it are skipped, unless the jobs use a
conditional statement that causes the job to continue."* 本 demo 实现基础语义：
依赖链上出现 FAILED ⇒ 传递 SKIPPED；不依赖失败 job 的分支照常执行。

## 对比 / 选型

| 维度 | stage barrier | needs DAG |
| --- | --- | --- |
| wall-clock | 长（短板 stage 拖累全部） | 短（关键路径决定） |
| 配置复杂度 | 低（只写 stage） | 中（每个 job 列 needs） |
| 适用规模 | 小项目 | 大项目 / monorepo 多条独立链路 |
| 失败隔离 | 差（整 stage 等待） | 好（只跳过依赖链上的 job） |

## 环境准备

- 操作系统：任意（纯计算模拟，无系统调用）
- 语言版本：C（C99）/ Python 3.8+ / Go 1.18+
- 依赖：无

## 运行方式

### C
```bash
gcc -O2 -Wall -Wextra dag_pipeline.c -o dag_pipeline && ./dag_pipeline
```
### Python
```bash
python3 dag_pipeline.py
```
### Go
```bash
go run dag_pipeline.go
```

## 关键代码片段（Python）

```python
def topo_layers(jobs):
    """Kahn 分层拓扑排序;有环抛 ValueError。"""
    succ = {j.name: [] for j in jobs}            # 邻接表: need -> 依赖它的 job
    in_deg = {j.name: 0 for j in jobs}
    for j in jobs:
        for n in j.needs:
            succ[n].append(j.name)
            in_deg[j.name] += 1
    queue = sorted(n for n, d in in_deg.items() if d == 0)
    layers, done = [], 0
    while queue:
        layers.append(queue)
        done += len(queue)
        nxt = []
        for u in queue:                          # 模拟"完成这批 job"
            for v in succ[u]:                   # 删出边 = 后继入度减 1
                in_deg[v] -= 1
                if in_deg[v] == 0:
                    nxt.append(v)
        queue = sorted(nxt)
    if done != len(jobs):                       # 出队总数 < 节点数 ⇒ 有环
        cyc = sorted(n for n, d in in_deg.items() if d > 0)
        raise ValueError(f"cycle detected among jobs: {', '.join(cyc)}")
    return layers


def earliest_finish(jobs):
    """关键路径:ef[j] = dur[j] + max(ef[need])。"""
    ef = {}
    for layer in topo_layers(jobs):              # 拓扑序保证 need 先算
        for name in layer:
            j = by_name(jobs, name)
            ef[name] = j.duration + max((ef[n] for n in j.needs), default=0)
    return ef
```

## 性能与边界

- 时间复杂度：拓扑排序/关键路径均为 O(V+E)；V=job 数，E=needs 边数（Kahn 算法与 DFS 拓扑
  排序同为 O(V+E)，但 Kahn 迭代实现天然按层并行、显式环检测更简单）
- 真实系统边界：GitHub Actions 单 job 默认超时 360 分钟；GitLab `needs` 有数量上限（历史
  版本曾限 50）；本 demo 假定无限并发（忽略 runner 池上限，实际系统需要考虑排队）

## 注意事项与常见坑

- **`needs` 指向不存在的 job**：GitLab 流水线创建直接报错 `'xxx' job depends on 'yyy' job,
  but 'yyy' job does not exist`；与 rules 组合时可用 `optional: true` 声明可选依赖。
  demo 的 `add_job` 同样在构建图时校验。
- **环不是运行时错误而是创建时错误**：必须在调度前用拓扑排序检测，否则调度器死等。
- **同一层的输出顺序不唯一**：拓扑排序只保证偏序；demo 内对每层排序（`sorted`）保证三种
  语言输出可复现比对。
- **wall-clock ≠ 计算量**：DAG 调度减少的是等待时间，消耗的总计算量不变（GitLab 博客
  明确指出这一点）。
- **失败传播的"除非"分支**：GitHub Actions 可用 `if: always()` 让失败后仍执行（如清理/
  通知 job），本 demo 只实现默认的跳过语义。

## 参考资料（实际阅读过的权威来源）

- [Pipeline architecture — GitLab Docs](https://archives.docs.gitlab.com/15.11/ee/ci/pipelines/pipeline_architectures.html) — Basic vs DAG 流水线官方对比，含菱形依赖 .gitlab-ci.yml 完整示例
- [使用 needs 让任务更早启动 — GitLab Docs](https://gitlab.cn/docs/jh/ci/yaml/needs/) — needs 语义原文：扇出/扇入/菱形/`needs: []` 立即启动/optional 可选依赖
- [Workflow syntax for GitHub Actions — GitHub Docs](https://docs.github.com/pt/enterprise-server@3.3/articles/workflow-syntax-for-github-actions/) — `jobs.<job_id>.needs` 定义与"失败则跳过依赖者"原文、job 默认并行
- [Get faster and more flexible pipelines with a DAG — GitLab Blog](https://about.gitlab.com/blog/directed-acyclic-graph/) — A(1min)/B(5min)/C(只依赖 A) 算例与 wall-clock 解释
- [Topological Sort Tutorial With Examples](https://kindatechnical.com/graph-theory/topological-sort-tutorial-with-examples.html) — Kahn 算法步骤与环检测（结果数 < 节点数）
- [Coding Interview: Topological Sort Deep Dive](https://www.techinterview.org/post/3233474202/coding-interview-topological-sort-deep-dive-kahn-algorithm-dfs-course-schedule-alien-dictionary-build-order-cycle-detection) — 关键路径公式 earliest_start/earliest_finish 及并行层数含义
