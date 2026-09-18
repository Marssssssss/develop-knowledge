// CPython 三代 GC 的判定逻辑模型(Go 版,对应 Python/gc.c;与 Python 版 cpy_gc.py 同源)。
//
// 权威来源(实际联网阅读):
//   docs.python.org/3/library/gc.html:
//     "The GC classifies objects into three generations depending on how many collection
//      sweeps they have survived. ... to decide when to run, the collector keeps track of
//      the number object allocations and deallocations since the last collection. When
//      the number of allocations minus the number of deallocations exceeds threshold0,
//      collection starts. Initially only generation 0 is examined. If generation 0 has
//      been examined more than threshold1 times since generation 1 has been examined,
//      then generation 1 is examined as well."
//     "gc.collect(...): The sum of collected objects and uncollectable objects is returned."
//     "gc.get_stats(): ... collections / collected / uncollectable"
//   CPython Python/gc.c:`record_allocation` 里 generations[0].count++ ;
//     `gc_collect_main` 里 `if (generation+1 < NUM_GENERATIONS)
//     generations[generation+1].count += 1;` 然后 `for (i = 0; i <= generation; i++)
//     generations[i].count = 0;` ;`gc_select_generation()` 从最老一代往下找第一个
//     count > threshold 的代号,但**若 i 是最老一代且 long_lived_pending <
//     long_lived_total / 4 就跳过**(硬编码 25%,避免"每固定次数分配就做一次全量收集"
//     导致 issue #4074 的二次复杂度);晋升时 `if (generation == NUM_GENERATIONS - 2)
//     long_lived_pending += gc_list_size(young)`,收到最老一代时
//     `long_lived_pending = 0; long_lived_total = gc_list_size(young)`。
//   CPython Include/internal/pycore_runtime_init.h 的默认阈值:
//     3.13+ 为 { 2000, 10, 10 };3.12 及更早为 { 700, 10, 10 }。
//     (真实解释器的实测断言在 Python 版 main.py 的 demo5 里 —— Go 侧无对应物。)
package main

const (
	numGenerations = 3
	longLivedRatio = 4 // 硬编码 25%:pending < total / 4 就不做全量收集
)

type tracked struct {
	oid           string
	gen           int
	reachable     bool
	uncollectable bool
}

type genStats struct {
	collections   int
	collected     int
	uncollectable int
}

type genState struct {
	thresholds       [numGenerations]int
	counts           [numGenerations]int
	objs             [numGenerations][]*tracked
	garbage          []*tracked
	longLivedTotal   int
	longLivedPending int
	stats            [numGenerations]genStats
}

func newGenState(threshold0, threshold1, threshold2 int) *genState {
	return &genState{thresholds: [numGenerations]int{threshold0, threshold1, threshold2}}
}

// recordAlloc:只有第 0 代的 count 增长。
func (s *genState) recordAlloc(n int) { s.counts[0] += n }

// recordDealloc:count **不会变负**(CPython 里是 `if (count > 0) count--;`)。
func (s *genState) recordDealloc(n int) {
	for i := 0; i < n; i++ {
		if s.counts[0] > 0 {
			s.counts[0]--
		}
	}
}

func (s *genState) track(oid string, reachable, uncollectable bool) *tracked {
	t := &tracked{oid: oid, reachable: reachable, uncollectable: uncollectable}
	s.objs[0] = append(s.objs[0], t)
	return t
}

// enabled:自动触发还要看 threshold0 是否为 0(为 0 即关闭收集)。
func (s *genState) enabled() bool {
	return s.counts[0] > s.thresholds[0] && s.thresholds[0] != 0
}

// selectGeneration 复现 gc_select_generation():返回要收集的最老代号;-1 表示本次不收集。
func (s *genState) selectGeneration() int {
	for i := numGenerations - 1; i >= 0; i-- {
		if s.counts[i] > s.thresholds[i] {
			if i == numGenerations-1 && s.longLivedPending < s.longLivedTotal/longLivedRatio {
				continue // 全量收集被 25% 启发式推迟
			}
			return i
		}
	}
	return -1
}

// collect 收掉 0..gen 各代;返回值 = collected + uncollectable(与 gc.collect() 一致)。
func (s *genState) collect(gen int) int {
	// a) 计数更新(顺序与 gc_collect_main 相同)
	if gen+1 < numGenerations {
		s.counts[gen+1]++
	}
	for i := 0; i <= gen; i++ {
		s.counts[i] = 0
	}

	// b) 把更年轻的各代并入被收集的最老代(gc_list_merge)
	var young []*tracked
	for g := 0; g <= gen; g++ {
		young = append(young, s.objs[g]...)
		s.objs[g] = nil
	}

	// c) 分出 存活 / 不可回收 / 已死
	collected, uncollectable := 0, 0
	var alive []*tracked
	for _, o := range young {
		switch {
		case o.uncollectable:
			uncollectable++
			s.garbage = append(s.garbage, o)
			s.stats[gen].uncollectable++
		case o.reachable:
			alive = append(alive, o)
		default:
			collected++
			s.stats[gen].collected++
		}
	}

	// d) 存活者晋升一代;两处 long_lived 记账
	if gen < numGenerations-1 {
		if gen == numGenerations-2 {
			s.longLivedPending += len(alive)
		}
		for _, o := range alive {
			o.gen = gen + 1
		}
		s.objs[gen+1] = append(s.objs[gen+1], alive...)
	} else {
		for _, o := range alive {
			o.gen = numGenerations - 1 // 封顶在最老代,不再晋升
		}
		s.objs[gen] = append(s.objs[gen], alive...)
		s.longLivedPending = 0
		s.longLivedTotal = len(s.objs[gen])
	}

	s.stats[gen].collections++
	return collected + uncollectable
}
