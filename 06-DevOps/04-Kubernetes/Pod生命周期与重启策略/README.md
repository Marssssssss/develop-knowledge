# Pod 生命周期与重启策略

## 简介

Kubernetes Pod 是调度的最小单位,但其生命周期比普通进程复杂得多:Pod 有 5 个**宏观 phase**(Pending / Running / Succeeded / Failed / Unknown),内部每个容器有自己的 restartPolicy(Always / OnFailure / Never),失败后会按 **指数退避** 等待重启(10s → 20s → 40s → 80s → 160s → 300s,上限 5 min)。Sidecar 容器(如日志收集、代理)无视 Pod-level 策略,总是按 container-level Always 重启,这是云原生"陪伴式"基础设施的核心理念。

**关键概念**:
- **Pod phase**:5 态有限状态机,描述 Pod 当前的高层阶段
- **restartPolicy**:Always(默认,任何退出都重启)/ OnFailure(只非零退出重启)/ Never(从不重启)
- **Exponential backoff**:连续崩溃时逐步延长等待,防止"故障容器风暴"压垮系统
- **Sidecar 独立性**:Sidecar(声明在 initContainers 中且 restartPolicy=Always 的容器)不受 Pod 级策略影响

## 原理详解

### Pod phase 状态机(摘自官方文档)

```
   ┌────────┐  至少一个 main 容器启动成功   ┌────────┐
   │Pending │ ────────────────────────────→ │Running │
   └───┬────┘                                └───┬────┘
       │ schedule 失败 / 镜像拉取失败 /          │ 所有 main 容器退出
       │ 等待资源                                │
       │ (Node 失联)                             ↓
       ↓                                    ┌────────┐    ┌────────┐
   ┌────────┐                                │Succeed │    │ Failed │
   │ Unknown│ ← 通信故障                       │(全 0)  │    │(有非0) │
   └────────┘                                └────────┘    └────────┘
```

简化规则(完整定义见官方表格):
- 至少一个 main 容器处于 Running 或正在启动 → `Running`
- 所有 main 容器退出码 0 → `Succeeded`
- 至少一个 main 容器退出码 ≠ 0 且不会重启 → `Failed`
- kubelet 通信中断 → `Unknown`

### restartPolicy 决策表(官方原文表格)

| 退出码 | Always | OnFailure | Never | Sidecar(总是 Always) |
|---|---|---|---|---|
| 0(成功) | 重启 | 不重启 | 不重启 | 重启 |
| 非 0(失败) | 重启 | 重启 | 不重启 | 重启 |

### 指数退避(10s/20s/40s/.../300s)

官方原文:
> "After containers in a Pod exit, the kubelet restarts them with an exponential backoff delay (10s, 20s, 40s, …), that is capped at 300 seconds (5 minutes). Once a container has executed for 10 minutes without any problems, the kubelet resets the restart backoff timer for that container."

序列: `10 → 20 → 40 → 80 → 160 → 300 → 300 → 300 ...`,上限 300 秒。
**重置条件**:容器连续正常运行 ≥ 10 min,backoff timer 清零(下次崩溃当首次处理)。

### Sidecar 独立性

官方原文:
> "Sidecar containers ignore Pod-level restartPolicy: in Kubernetes, a sidecar is defined as an entry inside initContainers that has its container-level restartPolicy set to Always."

Sidecar 通过 `initContainers` 字段声明 + 显式 `restartPolicy: Always` 实现,目的是让其独立于主应用容器的生命周期,持续提供日志/监控/代理等"陪伴式"服务。

## 对比 / 选型

| 场景 | 推荐 restartPolicy | 理由 |
|---|---|---|
| Web 服务、API、Deployment | `Always` | 默认,保持始终运行 |
| Job 批处理任务(可重试) | `OnFailure` | 失败重试,成功就退出 |
| Job 一次性迁移脚本 | `Never` | 失败留给上层处理,避免重试风暴 |
| 日志/监控/代理 Sidecar | `Always`(容器级) | 独立于主应用,持续运行 |
| Init Container | 同 Pod-level | 共享 Pod 的 restart 策略 |

## 环境准备

- **操作系统**:Windows / Linux / macOS 均可(纯算法模拟,不依赖内核特性)
- **语言版本**:
  - C:任意 C99 编译器(`gcc`/`clang`)
  - Python:3.10+
  - Go:1.18+
- **依赖**:无第三方依赖,仅标准库

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -std=c99 pod_lifecycle.c -o pod_lifecycle
./pod_lifecycle
```

### Python

```bash
python3 pod_lifecycle.py
```

### Go

```bash
go run pod_lifecycle.go
```

## 关键代码片段

### Python:决策表(should_restart)

```python
def should_restart(c: Container, pod_policy: str, exit_code: int) -> bool:
    """Decision table (kubernetes.io docs):
       exit | Always | OnFailure | Never
       0    | yes    | no        | no
       !=0  | yes    | yes       | no
    Sidecars override pod-level policy with container-level Always."""
    effective = Policy.ALWAYS if c.kind == Kind.SIDECAR else pod_policy
    if effective == Policy.ALWAYS:
        return True
    if effective == Policy.ONFAILURE:
        return exit_code != 0
    return False
```

### C:指数退避序列

```c
static int backoff_for(int restart_count) {
    static const int steps[] = {10, 20, 40, 80, 160, 300};
    int n = sizeof(steps) / sizeof(steps[0]);
    int idx = restart_count - 1;
    if (idx < 0) return 0;
    if (idx >= n) idx = n - 1;  /* 上限 5 min */
    return steps[idx];
}
```

### Go:Phase 计算

```go
func computePhase(p *Pod) Phase {
    mains := 0; running := 0; succeeded := 0; failed := 0
    for _, c := range p.Containers {
        if c.Kind != KindMain { continue }
        mains++
        switch c.State {
        case CtrRunning: running++
        case CtrTerminated:
            if c.LastExitCode == 0 { succeeded++ } else { failed++ }
        }
    }
    if running > 0 { return PhaseRunning }
    if failed > 0 { return PhaseFailed }
    if mains > 0 && succeeded == mains { return PhaseSucceeded }
    return PhasePending
}
```

## 性能与边界

- **时间复杂度**:`O(n)` per tick(n = 容器数),通常 n ≤ 10(实际生产极少超过 5)
- **空间复杂度**:`O(n)`,每容器 6 个字段
- **规模上限**:Pod 内容器数官方无硬限,但 K8s 推荐 ≤ 5,Sidecar 模式可能到 10+
- **退避上限**:300s(5 min),封顶后保持 300s 不再增长
- **退避重置**:连续正常运行 ≥ 10 min 重置;这是"稳定运行"判定的唯一标准

## 注意事项与常见坑

- **退避序列是"每次退出后"延迟**,不是"累计计时"。容器崩溃 → 等 backoff 秒 → 重启 → 再次崩溃 → backoff 索引 +1
- **Sidecar 必须是 initContainers 条目** + `restartPolicy: Always`,否则视为普通 init 容器,执行一次就退出
- **Pod Failed 与容器重启不冲突**:Pod 标记 Failed 是因为"所有容器都不会再重启了",不等于"被 kill"
- **`kubectl get pod` 显示的 Status ≠ Phase**:STATUS 是 kubectl 的可读字段(如 `CrashLoopBackOff`、`Terminating`),PHASE 是 API 模型字段
- **10 min 重置按容器维度**:一个容器独立计数,与其他容器无关;sidecar 重置不影响 main
- **initContainer 的 restartPolicy 继承 Pod-level**(除非是 sidecar);失败后若 Pod-level 是 Always/OnFailure,kubelet 会重启它
- **Job 强制 `restartPolicy: Never` 或 `OnFailure`**:Deployment 不允许 OnFailure/Never(API server 校验拒绝),避免用户配置错
- **1.27 之后删除前转 terminal phase**:Pod 被删除时,kubelet 先把 phase 转到 Succeeded/Failed 再从 API server 删除(静态 Pod 和 force delete 例外)

## 参考资料(实际阅读过的权威来源)

- [Pod Lifecycle | Kubernetes](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/) — phase 定义 + restartPolicy 表格 + 指数退避原文(10s/20s/40s/.../300s)+ 10 min 重置原文 + Sidecar 独立性原文
- [Pod 的生命周期 | Kubernetes 中文](https://kubernetes.io/zh-cn/docs/concepts/workloads/pods/pod-lifecycle/) — 中文版完整对照,验证中文术语一致性
- [Pod v1 API 参考](https://kubernetes.io/zh/docs/reference/kubernetes-api/workload-resources/pod-v1/) — `restartPolicy` / `terminationGracePeriodSeconds` / `activeDeadlineSeconds` 字段定义
- [Job 文档 | Kubernetes 中文](https://steering@kubernetes.io/zh-cn/docs/concepts/workloads/controllers/job) — Job 强制 `restartPolicy: Never` + `backoffLimit` + `podFailurePolicy` 的关系
