# HPA 期望副本数算法与被忽略的阻尼

## 简介

HPA 常被背成一句话:`desiredReplicas = ceil(currentReplicas × currentMetricValue / desiredMetricValue)`。这句话对,但**几乎从不是最终答案**。真实算法在它外面套了四层阻尼:

1. **容差(tolerance,默认 0.1)**:比值落在 `[0.9, 1.1]` 内直接不动。
2. **缺失指标的保守假设**:缩容时缺失 Pod 按 `max(100, target)%` 计,扩容时按 `0%` 计。
3. **未 Ready Pod 的保守假设**:扩容方向上未 Ready 的 Pod 一律按 `0%` 计。
4. **稳定窗口(stabilization window)**:缩容取窗口内**最大**推荐值,扩容取窗口内**最小**推荐值。

另外还有两道硬闸:**容差内的 usageRatio 依赖整数除法**(`currentUtilization = metricsTotal*100/requestsTotal`,截断),以及**单次扩容最多翻倍且不低于 4**(`scaleUpLimit = max(2×current, 4)`)。

本项目把这些逐条做成断言,并且**刻意构造了「阻尼改变结论」的对照用例** —— 否则阻尼只是注释里的一行字。

## 原理详解

### 基础公式与容差

```
desiredReplicas = ceil( currentReplicas × currentMetricValue / desiredMetricValue )
```

> 原文:*"The control plane skips any scaling action if the ratio is sufficiently close to 1.0 (within a configurable tolerance, 0.1 by default)."*

源码里容差是一个双向区间(不是绝对值),缩容与扩容可以分别配:

```go
func (t Tolerances) isWithin(usageRatio float64) bool {
    return (1.0-t.scaleDown) <= usageRatio && usageRatio <= (1.0+t.scaleUp)
}
```

### usageRatio 的整数除法陷阱

```go
currentUtilization = int32((metricsTotal * 100) / requestsTotal)  // 整数除法,截断
return float64(currentUtilization) / float64(targetUtilization)
```

`metricsTotal=100 / requestsTotal=300` 得到的是 **33** 而不是 33.33,`usageRatio` 因此是 0.66 而不是 0.6667。跨过 `ceil` 时这一步的 1 个百分点足以改变结果。

### 缺失指标:为什么缩容要往"重"里假设

```go
if usageRatio < 1.0 {
    fallbackUtilization := int64(max(100, targetUtilization))
    for podName := range missingPods {
        metrics[podName] = requests[podName] * fallbackUtilization / 100   // 按 100% 计
    }
} else if usageRatio > 1.0 {
    for podName := range missingPods { metrics[podName] = 0 }              // 按 0% 计
}
```

方向是**反直觉**的:要缩容时,缺失的 Pod 被当成「吃满 100%」,于是平均利用率被抬高,缩容被抑制;要扩容时,缺失 Pod 被当成「吃 0%」,平均利用率被压低,扩容也被抑制。**两个方向都在抑制动作** —— 这是设计意图,不是 bug。

本项目用例:4 副本、target=50,3 个 Pod 各 40m(request 100m)、1 个缺失。原始 `usageRatio = 40/50 = 0.8`(想缩);补上缺失 Pod 的 100m 后 `newUsageRatio = 55/50 = 1.1`,**恰好落在容差上界** → 维持 4。对照组去掉缺失 Pod 后是 `ceil(0.8×3) = 3`。同一份指标,有没有缺失 Pod 结论不同。

### 未 Ready Pod:只在扩容方向上生效

```go
scaleUpWithUnready := len(unreadyPods) > 0 && usageRatio > 1.0
...
if scaleUpWithUnready {
    for podName := range unreadyPods { metrics[podName] = 0 }
}
```

注意前提是 `usageRatio > 1.0`。本项目用例:1 个 Ready Pod 用 260m(request 100m)、1 个未 Ready(request 300m),target=50。原始 `usageRatio = 260/50 = 5.2`,补 0 后 `newUsageRatio = 65/50 = 1.3`,`ceil(1.3 × 2) = 3` —— 而忽略阻尼会得到 `ceil(5.2 × 1) = 6`,差一倍。

> 顺带一个容易踩的结论:当所有 Pod 的 request 相同时,「按 0% 补未 Ready Pod」与「用 readyPodCount 直接算」**数学上完全等价**。只有 request 不均时阻尼才看得出来,所以本项目故意用了 100m / 300m 的不均配置。

### 方向反转 → 不动

```go
if tolerances.isWithin(newUsageRatio) ||
   (usageRatio < 1.0 && newUsageRatio > 1.0) ||
   (usageRatio > 1.0 && newUsageRatio < 1.0) {
    return currentReplicas, ...
}
```

补完保守值后如果**缩放方向被翻转**,宁可什么都不做。

### 单次扩容上限

```go
func calculateScaleUpLimit(currentReplicas int32) int32 {
    return int32(math.Max(scaleUpLimitFactor*float64(currentReplicas), scaleUpLimitMinimum))
}
// scaleUpLimitFactor = 2.0, scaleUpLimitMinimum = 4.0
```

从 1 副本出发,单次最多到 4(而不是 `min(maxReplicas, 任意值)`);从 100 出发最多到 200。这就是 issue #32304 之后加的「防止 heapster/kubelet 报假 CPU 导致副本数暴涨」的保护。注意判据是 `hpaMax > scaleUpLimit`:当 `maxReplicas` 比 `scaleUpLimit` 还小时,限制条件变成 `TooManyReplicas`。

### 稳定窗口

```go
upRecommendation   = min(窗口内所有推荐值, desiredReplicas)   // 扩容
downRecommendation = max(窗口内所有推荐值, desiredReplicas)   // 缩容
```

- 缩容窗口默认 **5 分钟**(`--horizontal-pod-autoscaler-downscale-stabilization`),取窗口内最大值 → 缩容被拖慢。
- 扩容窗口默认 **0**,因此 `upCutoff = now`,任何历史记录都不满足 `> upCutoff`,等价于不生效 —— 扩容不被拖。

本项目对此有专门断言:同一份历史记录,扩容窗口设为 60s 时结果被压到 2,设为 0 时直接取 desired=4。

### 其它官方默认值(来自文档)

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--horizontal-pod-autoscaler-sync-period` | 15s | 控制循环间隔(不是连续进程) |
| `--horizontal-pod-autoscaler-downscale-stabilization` | 5min | 缩容稳定窗口 |
| `--horizontal-pod-autoscaler-cpu-initialization-period` | 5min | 启动期 CPU 忽略窗口 |
| `--horizontal-pod-autoscaler-initial-readiness-delay` | 30s | 初始 Ready 判定延迟 |
| tolerance | 0.1 | 双向容差 |

## 对比

| 环节 | 输入 | 输出 | 抑制方向 |
| --- | --- | --- | --- |
| 容差 | usageRatio | 是否动作 | 双向 |
| 缺失指标(缩容) | 按 100% 补 | newUsageRatio 抬高 | 抑制缩容 |
| 缺失指标(扩容) | 按 0% 补 | newUsageRatio 压低 | 抑制扩容 |
| 未 Ready(仅扩容) | 按 0% 补 | newUsageRatio 压低 | 抑制扩容 |
| 方向反转 | 新旧 ratio 符号 | 维持 | 抑制动作 |
| scaleUpLimit | current | 单次上限 | 抑制扩容 |
| 缩容稳定窗口 | 5min 内推荐 | 取最大 | 抑制缩容 |
| 扩容稳定窗口 | 默认 0 | 取最小(实际不生效) | 基本不抑制 |

## 环境

- Python 3.8+(仅标准库)
- Go 1.18+(仅标准库,无三方依赖)
- C99 编译器(仅标准库,需 `-lm`)

## 运行方式

```bash
python python/hpa_algorithm.py            # 40 项断言
go run go/hpa_algorithm.go
gcc -std=c99 -o /tmp/hpa c/hpa_algorithm.c -lm && /tmp/hpa
```

## 关键代码

```python
# 缩容:缺失 Pod 按 max(100, target)% 计;扩容:按 0% 计
if len(pods.missing) > 0:
    if usage_ratio < 1.0:
        fallback = max(100, target_utilization)
        for p in pods.missing:
            m[p] = requests[p] * fallback // 100
    elif usage_ratio > 1.0:
        for p in pods.missing:
            m[p] = 0

new_usage_ratio, _, _ = get_resource_utilization_ratio(m, requests, target_utilization)
if (tol.is_within(new_usage_ratio)
        or (usage_ratio < 1.0 and new_usage_ratio > 1.0)
        or (usage_ratio > 1.0 and new_usage_ratio < 1.0)):
    return current_replicas, utilization, "damped/reversed -> hold"
```

## 性能边界

- 复杂度 O(Pod 数),每 15s 一轮,与指标种类数线性相乘;多指标时每个 metric 各算一次再取最大。
- `ceil` 放大效应:副本数很大时,`ceil` 的 ±1 相对误差可忽略;副本数是 1~3 时,`ceil` 会造成「一步扩到 2 倍」的台阶。
- 整数除法在低利用率场景误差最大:真实 33.33% 被记成 33%,1 个 Pod 也能造成 3% 相对偏差。
- 稳定窗口是内存开销点:每个 HPA 保留窗口内的推荐值数组,窗口越长保留越多。
- 多指标中**任一**指标取不到值且其他指标建议缩容 → 缩容跳过;但只要有一个指标建议扩容,扩容仍会发生(文档原文)。

## 注意事项与常见坑

- **分母是 request,不是 limit**。容器没配 request 时 CPU 利用率**无定义**,HPA 对该指标**不采取任何动作** —— 这是「HPA 完全不动」最常见的原因。
- **HPA 不是连续控制**,是 15s 一轮的间歇控制循环,所以秒级抖动理论上观察不到。
- **status 里上报的是原始平均利用率**,不含缺失/未 Ready 的保守假设(文档明确说明),所以「status 显示 40% 却没缩容」是正常的。
- **缩容稳定窗口取最大、扩容取最小**,方向别记反;扩容窗口默认 0 意味着它平时形同虚设。
- **停止扩容的 `TooManyReplicas` 与 `ScaleUpLimit` 是两个不同 condition**,排查「扩不到 maxReplicas」时先看是哪个。
-  terminating Pod 对 per-pod 资源指标会被忽略,但对 object/external 指标仍然计数(只要还 Ready) —— 缩容瞬间的口径不一致就来自这里。
- 本 demo 只建模 CPU 资源型指标的 `GetResourceReplicas`;object / external / container-resource 走的是另外几个函数,容差与阻尼细节不同。

## 参考资料

已实际阅读:

1. Kubernetes 官方文档 — Horizontal Pod Autoscaling,https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/
2. `kubernetes/kubernetes@master` — `pkg/controller/podautoscaler/horizontal.go`,https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/controller/podautoscaler/horizontal.go
3. 同上 — `pkg/controller/podautoscaler/replica_calculator.go`,https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/controller/podautoscaler/replica_calculator.go
4. 同上 — `pkg/controller/podautoscaler/metrics/utilization.go`,https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/controller/podautoscaler/metrics/utilization.go
