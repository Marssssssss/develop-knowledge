# 蓝绿与金丝雀发布（部署策略模拟 + 分析门控）

## 简介

Kubernetes `Deployment` 原生只提供 RollingUpdate / Recreate 两种策略，蓝绿（Blue-Green）
和金丝雀（Canary）这类**渐进式交付（progressive delivery）**策略由 Argo Rollouts 等控制器
补充：控制新旧两个 ReplicaSet 的伸缩，用 Service 切换 / 按权重分流，并配合指标分析
（AnalysisRun）自动晋升或回滚。本 demo 用统一确定性随机数（LCG）模拟 1000 个请求，
逐一体会四种策略的**停机窗口、版本共存、爆炸半径与回滚成本**：

- **Recreate**：先删旧再启新 —— 保证两版本从不共存，但必然停机
- **RollingUpdate**：逐个替换实例 —— 无停机，但新旧共存窗口长
- **Blue-Green**：新旧两套全量并存，切流瞬时，回滚 = 切回（旧 RS 还在）
- **Canary**：按 setWeight 步进（10% → 33% → 100%），每步分析错误率，
  超阈值则中止（流量回到稳定版）

关键概念：

| 概念 | 一句话解释 |
| --- | --- |
| 爆炸半径（blast radius） | 新版本故障最多影响的流量比例 |
| activeService / previewService | 蓝绿的两组 Service：线上流量 vs 预览（新版本） |
| setWeight / steps | 金丝雀每步把多少百分比流量切给新版本 |
| AnalysisRun | 按指标（错误率、延迟）判定晋升或回滚的自动门控 |

## 原理详解

### 四种策略的请求路由（本 demo 的模拟模型）

```
Recreate:   旧███ 停机▒▒▒ 新███        (切换窗口内请求全部失败)
Rolling:    旧████ → 新▁旧███ → 新██▁旧█ → 新████   (逐实例替换,始终可服务)
BlueGreen:  旧████(active) + 新████(preview,不接流量)
            ── 切流:activeService selector 指向新 RS,瞬时 ──> 新████
Canary:     旧█████████▓  (10% 到新) ──分析──> 33% ──> 100%
                    ▲ 超阈值 => weight 归 0,流量全部回稳定版
```

### 金丝雀的步进与分析门控（Argo 语义）

```
for step in steps (10%, 33%, 100%):
    路由 weight% 的请求到新版本
    analysis: 新版本错误率 = new_err / new_total
    if 错误率 > threshold: abort —— weight=0,流量回稳定版,新 RS 缩容
if 全部步通过: 新 RS 标记 stable,旧 RS 缩容
```

### 蓝绿回滚为什么快

Argo 官方描述：蓝绿期间新旧两个 ReplicaSet 同时在线，只有旧版本接生产流量；切流只是
改 activeService 的 selector（或等效路由），**回滚 = 把 selector 改回去**，旧版本从未缩容，
所以是"瞬时"。代价是切换期间 2× 副本成本。

### Argo 官方选型表（原文归纳）

| 维度 | Blue-Green | 基础 Canary | Canary + 流量管理器 |
| --- | --- | --- | --- |
| 采用复杂度 | 低 | 中 | 高 |
| 是否需要流量管理器 | 否 | 否 | 是 |
| 兼容队列/锁型 worker | 是 | 否 | 否 |
| 流量切换 | 全有或全无 | 按百分比渐进 | 按百分比渐进 |
| 故障爆炸半径 | 大（全量） | 低 | 低 |

## 对比 / 选型

| 维度 | Recreate | Rolling | Blue-Green | Canary |
| --- | --- | --- | --- | --- |
| 停机 | 有 | 无 | 无 | 无 |
| 版本共存 | 从不 | 过渡期共存 | 全程两套 | 按权重共存 |
| 回滚速度 | 慢（重建） | 慢（再滚一轮） | 快（切回） | 快（weight 归 0） |
| 资源成本 | 1× | 1×+surge | 2× | 1×+canary 副本 |
| 爆炸半径 | 全量 | 部分实例 | 切流后全量 | 可控（≤weight） |

## 环境准备

- 操作系统：任意（纯计算模拟）
- 语言版本：C（C99）/ Python 3.8+ / Go 1.18+
- 依赖：无（三种语言使用同一 LCG：`state = 1664525·state + 1013904223 (mod 2³²)`，
  输出可跨语言比对）

## 运行方式

### C
```bash
gcc -O2 -Wall -Wextra deploy_demo.c -o deploy_demo && ./deploy_demo
```
### Python
```bash
python3 deploy_demo.py
```
### Go
```bash
go run deploy_demo.go
```

## 关键代码片段（Python）

```python
def simulate_canary(n, err_new, seed, steps=(10, 33, 100), threshold=0.05):
    """金丝雀:按步分权重;每步统计新版本错误率,超阈值即中止回滚。"""
    rng = LCG(seed)
    errors, batch = 0, n // len(steps)
    for w in steps:
        new_total = new_err = 0
        for _ in range(batch):
            if rng.rnd() < w / 100:        # 路由:weight% 到新版本
                new_total += 1
                if rng.rnd() < err_new:    # 新版本自身错误率
                    new_err += 1
                    errors += 1
            # 旧版本(稳定)错误率视为 0
        if new_total and new_err / new_total > threshold:
            return errors, f"aborted at {w}% (err={new_err}/{new_total})"
    return errors, "promoted to 100%"
```

## 性能与边界

- 模拟为 O(n)（n=请求总数）；真实系统的开销在路由层（Ingress/网格按权重分流）
- 金丝雀权重与副本数解耦：Argo 无流量管理器时按**副本数比例**近似权重（5 副本无法表达
  10% —— 需要流量管理器才能做细粒度切分，这是官方明确指出的边界）
- 蓝绿需要 2× 容量：对大内存服务可能不可行

## 注意事项与常见坑

- **Recreate 的"零共存"是特性不是 bug**：共享锁 / 互斥后台任务（队列 worker）必须用它，
  否则两版本同时消费队列会出数据问题（Argo 选型表：蓝绿"兼容队列 worker"也基于
  切流是原子的这一点，金丝雀则明确"否"）。
- **金丝雀无流量管理器时的权重粒度 = 副本粒度**：`setWeight: 10` 配 4 副本会被取整，
  想要 10% 需要 10+ 副本或接入 Istio/NGINX。
- **分析门控要留观察窗口**：demo 的错误率是瞬时统计，真实 AnalysisRun 需要按时间窗口
  聚合（如 5 分钟错误率），否则低流量步骤容易被单次请求扰动误判。
- **回滚不等于修复**：金丝雀 abort 后旧版本继续服务，但坏版本仍在集群里等排查；
  蓝绿切回后旧 RS 终究要被下一轮发布回收。
- **蓝绿的数据库迁移是隐形雷区**：两套应用版本共享同一个数据库，schema 必须双向兼容
  （业界通行 expand-contract 模式），否则"切回旧版本"时旧代码读不懂新 schema。

## 参考资料（实际阅读过的权威来源）

- [Concepts — Argo Rollouts 官方文档](https://argo-rollouts.readthedocs.io/en/release-1.8/concepts/) — 四种部署策略定义、渐进式交付概念、蓝绿/金丝雀选型表（是否需要流量管理器/队列 worker 兼容性/爆炸半径）原文
- [argo-rollouts GitHub 仓库 README](https://github.com/argoproj/argo-rollouts) — Rollout CRD 与 Deployment 的关系、blueGreen previewService/activeService 字段、canary setWeight 用例
- [Argo Rollouts 概念与架构解析（腾讯云开发者社区译文）](https://cloud.tencent.com/developer/article/1851444) — Rollout 控制器管理新旧 ReplicaSet、setWeight 步进示例 YAML、Analysis/AnalysisRun 架构
- [Directed Acyclic Graph / Pipeline architecture — GitLab Docs](https://archives.docs.gitlab.com/15.11/ee/ci/pipelines/pipeline_architectures.html) — CI 流水线与部署阶段的衔接语境（release 是 pipeline 的最后阶段）
