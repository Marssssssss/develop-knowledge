// Package main 转写 allpaths.c 的 compute_parallel_worker 与配套的并行计划代价模型。
package main

const (
	minTableScanPages = 1024 // 8MB / 8kB
	minIndexScanPages = 64   // 512kB / 8kB
	defaultMaxGather  = 2
	parallelSetupCost = 1000.0
	parallelTupleCost = 0.1
	intMax            = 1<<31 - 1
)

// Params 是一次 compute_parallel_worker 调用的输入。
type Params struct {
	HeapPages      int
	IndexPages     int
	MaxWorkers     int
	RelParallel    int  // reloption parallel_workers, -1 表示未设置
	IsBaseRel      bool // RELOPT_BASEREL
	MinTable       int
	MinIndex       int
}

// Log3Workers 对应源码里"阈值三倍三倍涨"的 while 循环。
func Log3Workers(pages, minPages int) int {
	if pages < 0 {
		return 0
	}
	threshold := minPages
	if threshold < 1 {
		threshold = 1
	}
	n := 1
	for int64(pages) >= int64(threshold)*3 {
		n++
		threshold *= 3
		if threshold > intMax/3 {
			break // 源码的溢出保护
		}
	}
	return n
}

// ComputeParallelWorker 返回 worker 数与人类可读的说明。
func ComputeParallelWorker(p Params) (int, string) {
	if p.MinTable == 0 {
		p.MinTable = minTableScanPages
	}
	if p.MinIndex == 0 {
		p.MinIndex = minIndexScanPages
	}
	if p.RelParallel != -1 {
		w := p.RelParallel
		if w > p.MaxWorkers {
			w = p.MaxWorkers
		}
		return w, "表级 reloption parallel_workers 直接决定, 再被 max_workers 截断"
	}
	if p.IsBaseRel && ((p.HeapPages >= 0 && p.HeapPages < p.MinTable) ||
		(p.IndexPages >= 0 && p.IndexPages < p.MinIndex)) {
		return 0, "BASEREL 且小于 min_parallel_*_scan_size, 直接返回 0"
	}
	workers := 0
	if p.HeapPages >= 0 {
		workers = Log3Workers(p.HeapPages, p.MinTable)
	}
	if p.IndexPages >= 0 {
		idx := Log3Workers(p.IndexPages, p.MinIndex)
		if workers > 0 {
			if idx < workers {
				workers = idx
			}
		} else {
			workers = idx
		}
	}
	capped := workers
	if capped > p.MaxWorkers {
		capped = p.MaxWorkers
	}
	return capped, "log3 公式给出, 再被 max_workers 截断"
}

// ParallelPlanCost Gather 之上的总代价: setup + 并行部分/(workers+1) + rows*tuple_cost。
func ParallelPlanCost(serialTotal, rows float64, workers int, leaderParticipation bool) float64 {
	if workers <= 0 {
		return serialTotal
	}
	share := serialTotal / float64(workers+1)
	if !leaderParticipation {
		share = serialTotal / float64(workers)
	}
	return parallelSetupCost + share + rows*parallelTupleCost
}

// Safety 是文档 15.2 的"何时不能用并行"判据。
type Safety struct {
	Writes            bool
	Suspendable       bool
	UnsafeFunction    bool
	NestedInParallel  bool
	MaxPerGather      int
}

// CanParallelize 返回是否可并行与原因列表。
func CanParallelize(s Safety) (bool, []string) {
	reasons := []string{}
	if s.MaxPerGather <= 0 {
		reasons = append(reasons, "max_parallel_workers_per_gather <= 0")
	}
	if s.Writes {
		reasons = append(reasons, "查询写数据或锁行")
	}
	if s.Suspendable {
		reasons = append(reasons, "查询可能被挂起(DECLARE CURSOR / PLpgSQL FOR 循环)")
	}
	if s.UnsafeFunction {
		reasons = append(reasons, "用到 PARALLEL UNSAFE 函数")
	}
	if s.NestedInParallel {
		reasons = append(reasons, "已经在一个并行查询里")
	}
	return len(reasons) == 0, reasons
}
