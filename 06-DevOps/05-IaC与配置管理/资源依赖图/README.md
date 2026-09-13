# Terraform 资源依赖图与拓扑排序

## 简介

Terraform 在 `terraform plan` / `terraform apply` 时构建一张**资源依赖图**(directed acyclic graph, DAG)来决定创建/更新/销毁顺序。本 demo 用 Python/C/Go 三语言实现 Kahn 拓扑排序、分层并行调度、环检测、apply/destroy 反序。

官方原话(HashiCorp Developer/Terraform Internals/Dependency Graph v1.15.x):
> "Terraform builds a dependency graph and uses it to perform operations, such as generate plans and refresh state."
> "graph walking is done in parallel: a node is walked as soon as all of its dependencies are walked. By default, up to 10 nodes in the graph will be processed concurrently. This number can be set using the `-parallelism` flag."

## 原理详解

### 节点类型(HashiCorp 官方规格)

`docs.hashicorp.com/terraform/internals/v1.15.x/graph` 列出 4 种节点:
- **Resource Node**:单资源(`count` > 1 时一个 node per count instance)
- **Provider Configuration Node**:provider 初始化节点,所有用此 provider 的 resource 都依赖它
- **Resource Meta-Node**:`count` > 1 时为视觉聚合,本身无 action
- **Root Node**:指向所有 resources,周游时忽略

### 构建图的 8 步骤(原文)

1. 添加 resource 节点(config 决定)
2. diff(plan)/state 附加 metadata 到节点
3. resource ↔ provisioner 映射
4. `depends_on` 显式依赖创建边
5. state 中存在但 config 已删的 "orphan" 节点加入(只记 metadata)
6. resource ↔ provider 映射
7. 解析表达式插值:引用某 resource 的属性 → 创建边 *referencer → referenced*
8. 创建 root 节点 → 验证无环、单根 → 走图

### 拓扑排序:Kahn 算法 + 分层

```
入度计数
↓
当前层 = 所有入度=0 节点(字典序排,确定性)
↓
出队本层节点 → 它们的 target 入度-1
↓
继续下一层
```

每层(同 timeline)= 一批可并行执行节点。HashiCorp spec 强调"a node is walked as soon as all of its dependencies are walked",即只要依赖满足就跑——分层只是工程简化,Kahn 本质一次走完。

### Apply vs Destroy 顺序

构造图同向时:**创建**沿正向、**销毁**沿反向。B 依赖 A → 创建时 A 先建 B 才能用 → 销毁时必须先删 B 才能删 A(`B 引用 A 的属性,删 A 后 B 的引用变 dangling`)。

KodeKloud/Terraform 官方一句话:
> "apply: following the edges; destroy: reversing the edges"

### parallelism 上限

Terraform 默认 10 路并行(`terraform apply -parallelism=N` 改)。Layer 内节点数超过 N → 拆为多批。本 demo `ScheduleParallel(N)` 实现此切片。

### 环检测

Kahn 出队节点数 < 节点总数 ⇒ 必有环。KodeKloud 文档也提到"Validate the graph has no cycles and has a single root"——环是 *创建时间错误*而非运行时间(terraform plan 阶段即可拒绝)。

## 对比

| 实现 | 用法 | 状态 |
| --- | --- | --- |
| `terraform graph` | DOT 格式输出,`dot -Tpng` 渲染图 | HashiCorp 一手 |
| `terraform plan` | 不画图,直接走图生成执行计划 | HashiCorp 一手 |
| 本 demo Kahn + 分层 | 命令行打印可并行层 + parallelism 切片 | 教学用 |

## 环境准备

- C:GCC 9+(`-Wall -Wextra -std=c99`),无外部依赖
- Python:3.10+(仅标准库)
- Go:1.18+

## 运行方式

```bash
# C
gcc -O2 -Wall -Wextra -std=c99 c/resource_graph.c -o resource_graph
./resource_graph

# Python
python3 python/resource_graph.py

# Go
go run go/resource_graph.go
```

## 关键代码片段

Python 版 Kahn 拓扑分层(`python/resource_graph.py`):

```python
def topo_layers(self) -> list[list[str]]:
    deg = self.in_degree()
    layers = []
    current = sorted([n for n, d in deg.items() if d == 0])
    while current:
        layers.append(current)
        next_layer = []
        for n in current:
            for t in self.edges.get(n, []):
                deg[t] -= 1
                if deg[t] == 0:
                    next_layer.append(t)
        current = sorted(next_layer)
    if sum(len(l) for l in layers) < len(self.nodes):
        raise ValueError("cycle detected; cannot topologically sort")
    return layers
```

对照 Terraform 官方 spec:"Walking the graph is done in parallel: a node is walked as soon as all of its dependencies are walked"——每层即一轮并行批次,层内节点可同步启动。

## 性能与边界

- 时间复杂度 **O(V + E)**(Kahn 标准)
- 空间 **O(V + E)**(邻接表)
- 不支持:多根节点 / 节点权重调度(关键路径) / 环的精确定位(此处仅给布尔判定)
- parallelism 默认 10 是 hashicorp/terraform 内部硬编码,可改 CLI flag

## 注意事项与常见坑

- **隐式 vs 显式依赖取舍**:KodeKloud 与 kkloudtarus 都强调"prefer implicit dependencies (attribute references) over `depends_on`"——因为 `depends_on` 无法知道依赖的哪个属性,plan 阶段必须保守替换资源。本 demo 案例 2 演示典型 `depends_on` 用例(实例未直接引用 IAM policy 但需要 policy 先 ready)
- **destroy 顺序颠倒是为何**:destroy 沿反图,看似与创建同序,本质是"删 B 才能删 A 的引用关系";但 destroy 时 Terraform 物理执行是按反序
- **并行 ≠ 加快**:HashiCorp 官方:"It may be helpful in certain special use cases or to help debug Terraform issues. Note that some providers (AWS, for example), handle API rate limiting issues at a lower level by implementing graceful backoff/retry in their respective API clients. For this reason, Terraform does not use this parallelism feature to address API rate limits directly."
- **环是配置错误**:spec 明文"Validate the graph has no cycles and has a single root"——环出现在 `depends_on` 或循环引用 config 必须改;Terraform 0.x 旧版曾因 plan 阶段未校验可陷入死锁,现代版本 plan 阶段直接 reject
- **模块级隐式依赖的成本**:oneuptime 文章指出"passing a module output to another module creates dependency on entire module"——若 networking module 有 50 资源,compute module 全等 50 个完成,即便实际只需要 vpc_id/subnet_id 两个值

## 参考资料(实际阅读过的权威来源)

- [Terraform Internals: Dependency Graph v1.15.x](https://docs.hashicorp.com/terraform/internals/v1.15.x/graph) — 全文阅读:4 种节点类型 + 8 步建图 + DFS 遍历 + 并行上限 10 + `-parallelism` flag
- [KodeKloud Notes: Understanding the Resource Graph](https://notes.kodekloud.com/docs/HashiCorp-Certified-Terraform-Associate-004/The-Core-terraform-Workflow/Understanding-the-Resource-Graph/page) — 全文阅读:implicit/explicit 依赖对比表 + "By default Terraform runs up to 10 operations in parallel"
- [kindatechnical.com: Terraform Execution Model](https://kindatechnical.com/terraform-fundamentals/terraform-execution-model.html) — 全文阅读:7 步流水线(parse → build graph → refresh → diff → plan → apply → update state) + ECS 实例隐式依赖菱形 demo
- [kkloudtarus: The Dependency Graph](https://kkloudtarus.net/en/blog/terraform-dependency-graph-depends-on-target) — 全文阅读:destroy 反向遍历图原理 + DOT 渲染命令行 `terraform graph | dot -Tpng > graph.png`
- [oneuptime: Terraform Dependency Analysis for Performance](https://oneuptime.com/blog/post/2026-02-23-how-to-use-terraform-dependency-analysis-for-performance/view) — 全文阅读:关键路径分析 + 模块级多余依赖误用警示
