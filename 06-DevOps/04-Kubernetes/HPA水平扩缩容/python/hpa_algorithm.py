"""
HPA(HorizontalPodAutoscaler)期望副本数算法 —— 逐行对齐 upstream 源码的最小实现。

权威来源(实际读过):
  1. https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/
  2. https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/controller/podautoscaler/horizontal.go
  3. .../replica_calculator.go
  4. .../metrics/utilization.go

从源码逐条摘出来的事实(不凭记忆):
  - Tolerances.isWithin(r): (1.0-scaleDown) <= r && r <= (1.0+scaleUp),默认 0.1
  - GetResourceUtilizationRatio: currentUtilization = int32(metricsTotal*100/requestsTotal)
    (**整数除法截断**),usageRatio = float64(currentUtilization)/float64(targetUtilization)
  - 缺失指标:缩容时按 max(100, targetUtilization)% 计;扩容时按 0% 计
  - 未 Ready 且 usageRatio>1.0 时(scaleUpWithUnready):未 Ready 的 Pod 按 0% 计
  - 用补过的值重算后:若在 tolerance 内 **或缩放方向被反转** → 维持当前副本数
  - calculateScaleUpLimit(current) = max(2.0*current, 4.0)
  - 稳定窗口:扩容取窗口内 **最小** 推荐值,缩容取窗口内 **最大** 推荐值
    (scaleUp 的 StabilizationWindowSeconds 默认为 0,故扩容通常不被稳定窗口拖住)
  - 缩容稳定窗口默认值 5 分钟,由 --horizontal-pod-autoscaler-downscale-stabilization 控制
"""
from typing import Dict, List, Tuple
import math

DEFAULT_TOLERANCE = 0.1
SCALE_UP_LIMIT_FACTOR = 2.0
SCALE_UP_LIMIT_MINIMUM = 4.0
DEFAULT_DOWNSCALE_STABILIZATION_SECONDS = 300


# ---------------------------------------------------------------- 基础算子
class Tolerances:
    """对应 replica_calculator.go 的 Tolerances。"""

    def __init__(self, scale_up: float = DEFAULT_TOLERANCE,
                 scale_down: float = DEFAULT_TOLERANCE):
        self.scale_up = scale_up
        self.scale_down = scale_down

    def is_within(self, usage_ratio: float) -> bool:
        return (1.0 - self.scale_down) <= usage_ratio <= (1.0 + self.scale_up)


def get_resource_utilization_ratio(metrics: Dict[str, int], requests: Dict[str, int],
                                   target_utilization: int) -> Tuple[float, int, int]:
    """对应 metrics/utilization.go:GetResourceUtilizationRatio。

    注意 currentUtilization 是 **整数除法** 结果,这一步的截断会真实影响 usageRatio。
    """
    metrics_total = 0
    requests_total = 0
    num_entries = 0
    for pod, v in metrics.items():
        if pod not in requests:
            continue                      # missing requests == extraneous metrics
        metrics_total += v
        requests_total += requests[pod]
        num_entries += 1
    if requests_total == 0:
        raise ValueError("no metrics returned matched known pods")
    current_utilization = (metrics_total * 100) // requests_total     # 截断!
    raw_average = metrics_total // num_entries if num_entries else 0
    return current_utilization / float(target_utilization), current_utilization, raw_average


def ceil_to_int32(x: float) -> int:
    return int(math.ceil(x))


def calculate_scale_up_limit(current_replicas: int) -> int:
    return int(max(SCALE_UP_LIMIT_FACTOR * current_replicas, SCALE_UP_LIMIT_MINIMUM))


# ---------------------------------------------------------------- 核心算法
class Pods:
    """groupPods 的极简版:把 Pod 分成 ready / unready / missing。"""

    def __init__(self, ready: List[str], unready: List[str], missing: List[str]):
        self.ready = ready
        self.unready = unready
        self.missing = missing


def get_resource_replicas(current_replicas: int, target_utilization: int,
                          pods: Pods, metrics: Dict[str, int],
                          requests: Dict[str, int],
                          tol: Tolerances) -> Tuple[int, int, str]:
    """对应 replica_calculator.go:GetResourceReplicas。返回 (replicaCount, utilization, 说明)。"""
    ready_pod_count = len(pods.ready)
    usage_ratio, utilization, _ = get_resource_utilization_ratio(
        metrics, requests, target_utilization)

    scale_up_with_unready = len(pods.unready) > 0 and usage_ratio > 1.0

    # 无 unready 且无 missing → 直接算
    if not scale_up_with_unready and len(pods.missing) == 0:
        if tol.is_within(usage_ratio):
            return current_replicas, utilization, "within tolerance"
        return ceil_to_int32(usage_ratio * ready_pod_count), utilization, "plain"

    m = dict(metrics)
    if len(pods.missing) > 0:
        if usage_ratio < 1.0:
            # 缩容:缺失 Pod 按 max(100, target)% 计
            fallback = max(100, target_utilization)
            for p in pods.missing:
                m[p] = requests[p] * fallback // 100
        elif usage_ratio > 1.0:
            # 扩容:缺失 Pod 按 0% 计
            for p in pods.missing:
                m[p] = 0
    if scale_up_with_unready:
        for p in pods.unready:
            m[p] = 0

    new_usage_ratio, _, _ = get_resource_utilization_ratio(m, requests, target_utilization)

    if (tol.is_within(new_usage_ratio)
            or (usage_ratio < 1.0 and new_usage_ratio > 1.0)
            or (usage_ratio > 1.0 and new_usage_ratio < 1.0)):
        return current_replicas, utilization, "damped/reversed -> hold"

    new_replicas = ceil_to_int32(new_usage_ratio * len(m))
    if ((new_usage_ratio < 1.0 and new_replicas > current_replicas)
            or (new_usage_ratio > 1.0 and new_replicas < current_replicas)):
        return current_replicas, utilization, "direction flip by metrics length"
    return new_replicas, utilization, "damped"


def normalize_desired_replicas(current_replicas: int, desired_replicas: int,
                               hpa_min: int, hpa_max: int) -> Tuple[int, str]:
    """对应 horizontal.go:normalizeDesiredReplicasWithBehaviors 里的 min/max + scaleUpLimit。"""
    minimum_allowed = hpa_min
    scale_up_limit = calculate_scale_up_limit(current_replicas)
    if hpa_max > scale_up_limit:
        maximum_allowed, cond, reason = scale_up_limit, "ScaleUpLimit", \
            "the desired replica count is increasing faster than the maximum scale rate"
    else:
        maximum_allowed, cond, reason = hpa_max, "TooManyReplicas", \
            "the desired replica count is more than the maximum replica count"
    if desired_replicas < minimum_allowed:
        return minimum_allowed, "TooFewReplicas"
    if desired_replicas > maximum_allowed:
        return maximum_allowed, cond
    return desired_replicas, "DesiredWithinRange"


# ---------------------------------------------------------------- 稳定窗口
class Recommendation:
    def __init__(self, value: int, timestamp: float):
        self.value = value
        self.timestamp = timestamp


class StabBehavior:
    def __init__(self, stabilization_window_seconds: int):
        self.stabilization_window_seconds = stabilization_window_seconds


def stabilize_recommendation(current_replicas: int, desired_replicas: int, now: float,
                             recommendations: List[Recommendation],
                             up: StabBehavior, down: StabBehavior) -> int:
    """对应 horizontal.go:stabilizeRecommendationWithBehaviors。

    关键点:扩容取窗口内 **最小**,缩容取窗口内 **最大**。
    """
    up_recommendation = desired_replicas
    up_cutoff = now - up.stabilization_window_seconds
    down_recommendation = desired_replicas
    down_cutoff = now - down.stabilization_window_seconds

    for rec in recommendations:
        if rec.timestamp > up_cutoff:
            up_recommendation = min(rec.value, up_recommendation)
        if rec.timestamp > down_cutoff:
            down_recommendation = max(rec.value, down_recommendation)

    recommendation = current_replicas
    if recommendation < up_recommendation:
        recommendation = up_recommendation
    if recommendation > down_recommendation:
        recommendation = down_recommendation
    return recommendation


def maybe_init_scale_down_stabilization_window(window_seconds):
    """对应 horizontal.go:maybeInitScaleDownStabilizationWindow(为 nil 时填默认 300s)。"""
    if window_seconds is None:
        return DEFAULT_DOWNSCALE_STABILIZATION_SECONDS
    return window_seconds




if __name__ == "__main__":
    from selfcheck_hpa_algorithm import selfcheck
    selfcheck()
