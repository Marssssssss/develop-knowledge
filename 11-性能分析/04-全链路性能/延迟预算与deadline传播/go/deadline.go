// Package deadline 实现延迟预算的下发：deadline（时刻）与 timeout（时长）的互换与传播。
//
// 口径来自 gRPC 官方 Deadlines 指南：
//   - deadline 是"客户端不愿再等的时刻"，timeout 是"最长持续时长"；
//   - 线路上传 timeout（已扣除已耗时），进程内部用 deadline，以此屏蔽时钟偏移；
//   - 客户端超时是 DEADLINE_EXCEEDED，服务端自动取消是 CANCELLED，两者不是一回事；
//   - 不设 deadline 等于无限等待（gRPC 默认即为不设）。
//
// Go 侧额外记录两个语言层面的坑：
//   - 时刻用 int64 纳秒而不是 float64 秒，避免长时间运行后的精度损失；
//     本包为了与 Python 版逐值对拍，仍用 float64 秒，但对外只暴露整毫秒接口；
//   - 剩余时长被 reserve 吃光时必须显式截断到 0，不能返回负数——
//     gRPC 的实现会把负值当"已过期"，而业务代码里负 timeout 常被误解成"不限时"。
package deadline

import "sort"

// 状态常量。
const (
	// OK 表示在预算内完成。
	OK = "OK"
	// DeadlineExceeded 是客户端侧的失败状态。
	DeadlineExceeded = "DEADLINE_EXCEEDED"
	// Cancelled 是服务端自动取消的状态。
	Cancelled = "CANCELLED"
)

// ToTimeout 把绝对 deadline 换算成剩余时长。
func ToTimeout(deadlineAbs, now float64) float64 { return deadlineAbs - now }

// ToDeadline 把线路上的 timeout 换算成本进程的绝对 deadline。
func ToDeadline(timeout, now float64) float64 { return now + timeout }

// Propagate 换算下游 timeout：扣掉已耗时，再扣掉本跳预留，且截断到 0。
func Propagate(deadlineAbs, now, reserve float64) float64 {
	r := ToTimeout(deadlineAbs, now) - reserve
	if r < 0 {
		return 0
	}
	return r
}

// ClientStatus 判定客户端状态：恰好等于 deadline 仍算成功（闭区间）。
func ClientStatus(deadlineAbs, finish float64) string {
	if finish <= deadlineAbs {
		return OK
	}
	return DeadlineExceeded
}

// ServerStatus 判定服务端状态：越过 deadline 即自动取消。
func ServerStatus(deadlineAbs, now float64) string {
	if now <= deadlineAbs {
		return OK
	}
	return Cancelled
}

// SplitEqual 把预算等分给 n 跳。
func SplitEqual(total float64, n int) []float64 {
	if n <= 0 {
		return nil
	}
	out := make([]float64, n)
	for i := range out {
		out[i] = total / float64(n)
	}
	return out
}

// SplitByCost 按历史成本加权；全零成本退化为等分。
func SplitByCost(total float64, costs []float64) []float64 {
	sum := 0.0
	for _, c := range costs {
		sum += c
	}
	if sum <= 0 || len(costs) == 0 {
		return SplitEqual(total, len(costs))
	}
	out := make([]float64, len(costs))
	for i, c := range costs {
		out[i] = total * c / sum
	}
	return out
}

// HeadroomResult 是注水法的分配结果。
type HeadroomResult struct {
	Alloc    []float64
	Feasible bool
	Slack    float64
}

// SplitByHeadroom 先给每跳下限，再把余量按"可压缩空间"比例分摊并封顶于上限。
func SplitByHeadroom(total float64, floors, caps []float64) HeadroomResult {
	n := len(floors)
	if n == 0 {
		return HeadroomResult{Feasible: true, Slack: total}
	}
	floorSum := 0.0
	for _, f := range floors {
		floorSum += f
	}
	if floorSum > total+1e-9 {
		out := append([]float64(nil), floors...)
		return HeadroomResult{Alloc: out, Feasible: false, Slack: total - floorSum}
	}
	head := make([]float64, n)
	headSum := 0.0
	for i := range floors {
		head[i] = caps[i] - floors[i]
		if head[i] < 0 {
			head[i] = 0
		}
		headSum += head[i]
	}
	extra := total - floorSum
	alloc := make([]float64, n)
	if headSum <= 0 {
		for i := range floors {
			alloc[i] = minFloat(caps[i], floors[i])
		}
	} else {
		for i := range floors {
			alloc[i] = minFloat(caps[i], floors[i]+extra*head[i]/headSum)
		}
	}
	used := 0.0
	for _, a := range alloc {
		used += a
	}
	return HeadroomResult{Alloc: alloc, Feasible: true, Slack: total - used}
}

// Reconcile 逐跳对账。
type Reconcile struct {
	Budget  float64
	Actual  float64
	Over    float64
	OverPct float64
}

// Consumed 返回每一跳的预算/实际/超支额与超支比例。
func Consumed(alloc, actual []float64) []Reconcile {
	n := len(alloc)
	if len(actual) < n {
		n = len(actual)
	}
	out := make([]Reconcile, 0, n)
	for i := 0; i < n; i++ {
		r := Reconcile{Budget: alloc[i], Actual: actual[i], Over: actual[i] - alloc[i]}
		if alloc[i] > 0 {
			r.OverPct = r.Over / alloc[i]
		} else {
			r.OverPct = 1.0 / 0.0 // +Inf：零预算下任何耗时都是无穷大比例
		}
		out = append(out, r)
	}
	return out
}

// WorstHops 返回超支最严重的若干跳（按超支额降序），供看板直接取 TopN。
func WorstHops(rows []Reconcile, n int) []Reconcile {
	sorted := append([]Reconcile(nil), rows...)
	sort.SliceStable(sorted, func(i, j int) bool { return sorted[i].Over > sorted[j].Over })
	if n < 0 || n > len(sorted) {
		n = len(sorted)
	}
	return sorted[:n]
}

func minFloat(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
