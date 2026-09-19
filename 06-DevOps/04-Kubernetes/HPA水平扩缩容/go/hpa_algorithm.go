// HPA(HorizontalPodAutoscaler)期望副本数算法 (Go),逐行对齐 upstream 源码。
//
// 权威来源(实际读过):
//   1. https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/
//   2. https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/controller/podautoscaler/horizontal.go
//   3. .../replica_calculator.go
//   4. .../metrics/utilization.go
//
// 关键事实:usageRatio 依赖 **整数除法** 的 utilization;缺失指标缩容按 max(100,target)%、
// 扩容按 0%;方向反转则维持;scaleUpLimit=max(2*current,4);稳定窗口扩收取 min、缩容取 max。
package main

import (
	"fmt"
	"math"
)

const (
	defaultTolerance                  = 0.1
	scaleUpLimitFactor                = 2.0
	scaleUpLimitMinimum               = 4.0
	defaultDownscaleStabilizationSecs = 300
)

type Tolerances struct{ ScaleUp, ScaleDown float64 }

func (t Tolerances) isWithin(r float64) bool {
	return (1.0-t.ScaleDown) <= r && r <= (1.0+t.ScaleUp)
}

// getResourceUtilizationRatio:currentUtilization = (metricsTotal*100)/requestsTotal,整数除法。
func getResourceUtilizationRatio(metrics, requests map[string]int64, target int64) (float64, int64) {
	var mt, rt int64
	for p, v := range metrics {
		r, ok := requests[p]
		if !ok {
			continue
		}
		mt += v
		rt += r
	}
	if rt == 0 {
		panic("no metrics returned matched known pods")
	}
	cur := (mt * 100) / rt // 截断
	return float64(cur) / float64(target), cur
}

func ceilToInt32(x float64) int32 { return int32(math.Ceil(x)) }

func calculateScaleUpLimit(current int32) int32 {
	return int32(math.Max(scaleUpLimitFactor*float64(current), scaleUpLimitMinimum))
}

type Pods struct{ Ready, Unready, Missing []string }

// getResourceReplicas 对应 replica_calculator.go:GetResourceReplicas。
func getResourceReplicas(current, target int32, pods Pods,
	metrics, requests map[string]int64, tol Tolerances) (int32, int64, string) {

	usageRatio, utilization := getResourceUtilizationRatio(metrics, requests, target)
	scaleUpWithUnready := len(pods.Unready) > 0 && usageRatio > 1.0

	if !scaleUpWithUnready && len(pods.Missing) == 0 {
		if tol.isWithin(usageRatio) {
			return current, utilization, "within tolerance"
		}
		return ceilToInt32(usageRatio * float64(len(pods.Ready))), utilization, "plain"
	}

	m := map[string]int64{}
	for k, v := range metrics {
		m[k] = v
	}
	if len(pods.Missing) > 0 {
		if usageRatio < 1.0 {
			fallback := int64(100)
			if target > fallback {
				fallback = target
			}
			for _, p := range pods.Missing {
				m[p] = requests[p] * fallback / 100
			}
		} else if usageRatio > 1.0 {
			for _, p := range pods.Missing {
				m[p] = 0
			}
		}
	}
	if scaleUpWithUnready {
		for _, p := range pods.Unready {
			m[p] = 0
		}
	}

	newRatio, _ := getResourceUtilizationRatio(m, requests, target)
	if tol.isWithin(newRatio) ||
		(usageRatio < 1.0 && newRatio > 1.0) ||
		(usageRatio > 1.0 && newRatio < 1.0) {
		return current, utilization, "damped/reversed -> hold"
	}
	newReplicas := ceilToInt32(newRatio * float64(len(m)))
	if (newRatio < 1.0 && newReplicas > current) || (newRatio > 1.0 && newReplicas < current) {
		return current, utilization, "direction flip by metrics length"
	}
	return newReplicas, utilization, "damped"
}

func normalizeDesiredReplicas(current, desired, hpaMin, hpaMax int32) (int32, string) {
	maximumAllowed := hpaMax
	cond := "TooManyReplicas"
	if lim := calculateScaleUpLimit(current); hpaMax > lim {
		maximumAllowed = lim
		cond = "ScaleUpLimit"
	}
	if desired < hpaMin {
		return hpaMin, "TooFewReplicas"
	}
	if desired > maximumAllowed {
		return maximumAllowed, cond
	}
	return desired, "DesiredWithinRange"
}

type Recommendation struct {
	Value     int32
	Timestamp float64
}
type StabBehavior struct{ Window float64 }

func stabilizeRecommendation(current, desired int32, now float64,
	recs []Recommendation, up, down StabBehavior) int32 {

	upRec, downRec := desired, desired
	upCutoff, downCutoff := now-up.Window, now-down.Window
	for _, r := range recs {
		if r.Timestamp > upCutoff && r.Value < upRec {
			upRec = r.Value
		}
		if r.Timestamp > downCutoff && r.Value > downRec {
			downRec = r.Value
		}
	}
	rec := current
	if rec < upRec {
		rec = upRec
	}
	if rec > downRec {
		rec = downRec
	}
	return rec
}

var checks int

func ck(cond bool, msg string) {
	if !cond {
		panic("assert failed: " + msg)
	}
	checks++
}

func main() {
	tol := Tolerances{defaultTolerance, defaultTolerance}

	ck(tol.isWithin(1.0), "1.0 在容差内")
	ck(tol.isWithin(0.90) && tol.isWithin(1.10), "端点含")
	ck(!tol.isWithin(0.899) && !tol.isWithin(1.101), "越界应拒绝")

	// 整数除法截断
	ratio, util := getResourceUtilizationRatio(
		map[string]int64{"a": 100}, map[string]int64{"a": 300}, 50)
	ck(util == 33, fmt.Sprintf("utilization 应为 33, 实得 %d", util))
	ck(math.Abs(ratio-33.0/50.0) < 1e-12, fmt.Sprintf("ratio 应为 0.66, 实得 %v", ratio))

	// 基础公式
	reqs := map[string]int64{"p1": 100, "p2": 100, "p3": 100}
	rep, u, why := getResourceReplicas(3, 100, Pods{Ready: []string{"p1", "p2", "p3"}},
		map[string]int64{"p1": 200, "p2": 200, "p3": 200}, reqs, tol)
	ck(u == 200, fmt.Sprintf("utilization 200, 实得 %d", u))
	ck(rep == 6 && why == "plain", fmt.Sprintf("期望 6/plain, 实得 %d/%s", rep, why))

	// 容差内不动 / 越界才动
	rep, _, why = getResourceReplicas(3, 100, Pods{Ready: []string{"p1", "p2", "p3"}},
		map[string]int64{"p1": 105, "p2": 105, "p3": 105}, reqs, tol)
	ck(rep == 3 && why == "within tolerance", fmt.Sprintf("1.05 应维持, 实得 %d/%s", rep, why))
	rep, _, _ = getResourceReplicas(3, 100, Pods{Ready: []string{"p1", "p2", "p3"}},
		map[string]int64{"p1": 111, "p2": 111, "p3": 111}, reqs, tol)
	ck(rep == 4, fmt.Sprintf("ceil(1.11*3)=4, 实得 %d", rep))

	// scaleUpLimit
	ck(calculateScaleUpLimit(1) == 4, "current=1 → 4")
	ck(calculateScaleUpLimit(3) == 6, "current=3 → 6")
	v, cond := normalizeDesiredReplicas(1, 10, 1, 100)
	ck(v == 4 && cond == "ScaleUpLimit", fmt.Sprintf("单次上限 4, 实得 %d/%s", v, cond))
	v, cond = normalizeDesiredReplicas(5, 1, 2, 100)
	ck(v == 2 && cond == "TooFewReplicas", fmt.Sprintf("min 生效, 实得 %d/%s", v, cond))
	v, cond = normalizeDesiredReplicas(20, 40, 1, 30)
	ck(v == 30 && cond == "TooManyReplicas", fmt.Sprintf("hpaMax 生效, 实得 %d/%s", v, cond))

	// 缺失指标 + 缩容:按 max(100,target)% 补 → 1.1 落回容差 → 维持
	reqs4 := map[string]int64{"p1": 100, "p2": 100, "p3": 100, "p4": 100}
	rep, u, why = getResourceReplicas(4, 50,
		Pods{Ready: []string{"p1", "p2", "p3"}, Missing: []string{"p4"}},
		map[string]int64{"p1": 40, "p2": 40, "p3": 40}, reqs4, tol)
	ck(u == 40, fmt.Sprintf("原始 utilization 40, 实得 %d", u))
	ck(rep == 4 && why == "damped/reversed -> hold", fmt.Sprintf("阻尼后维持 4, 实得 %d/%s", rep, why))
	rep, _, _ = getResourceReplicas(4, 50, Pods{Ready: []string{"p1", "p2", "p3"}},
		map[string]int64{"p1": 40, "p2": 40, "p3": 40}, reqs, tol)
	ck(rep == 3, fmt.Sprintf("无缺失时应缩到 3, 实得 %d", rep))

	// 未 Ready + 扩容(requests 不均):阻尼后 3,忽略阻尼是 6
	rep, _, why = getResourceReplicas(2, 50, Pods{Ready: []string{"p1"}, Unready: []string{"p2"}},
		map[string]int64{"p1": 260}, map[string]int64{"p1": 100, "p2": 300}, tol)
	ck(rep == 3 && why == "damped", fmt.Sprintf("阻尼后 3, 实得 %d/%s", rep, why))
	ck(ceilToInt32(5.2*1) == 6, "忽略阻尼的对照值应为 6")

	// 方向反转 → 维持
	rep, _, why = getResourceReplicas(2, 50, Pods{Ready: []string{"p1"}, Unready: []string{"p2"}},
		map[string]int64{"p1": 100}, map[string]int64{"p1": 100, "p2": 300}, tol)
	ck(rep == 2 && why == "damped/reversed -> hold", fmt.Sprintf("反转维持 2, 实得 %d/%s", rep, why))

	// 稳定窗口
	now := 1000.0
	down := StabBehavior{defaultDownscaleStabilizationSecs}
	recs := []Recommendation{{2, now - 60}, {1, now - 10}}
	ck(stabilizeRecommendation(5, 1, now, recs, StabBehavior{0}, down) == 2, "缩容取窗口内最大 2")
	ck(stabilizeRecommendation(5, 1, now, []Recommendation{{2, now - 400}}, StabBehavior{0}, down) == 1,
		"窗口外不参与")
	ck(stabilizeRecommendation(1, 4, now, []Recommendation{{2, now - 10}}, StabBehavior{60}, down) == 2,
		"扩容取窗口内最小 2")
	ck(stabilizeRecommendation(1, 4, now, []Recommendation{{2, now - 10}}, StabBehavior{0}, down) == 4,
		"扩容窗口 0 直接取 desired")

	fmt.Printf("hpa_algorithm(go): %d assertions passed\n", checks)
}
