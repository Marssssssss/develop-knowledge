// cProfile 输出解读复刻：确定性插桩事件流 → ncalls(total/primitive)/tottime/cumtime/percall。
//
// 口径（Python 官方文档 The Python Profilers，本轮实读）：
//   - 确定性剖析：所有函数调用、返回、异常事件都被监控，事件间精确计时
//   - tottime：该函数内的总时间，不含子函数；cumtime：含子函数（从调用到退出）
//   - ncalls 双数字 total/primitive：primitive = 非递归引起的调用；不递归只印单数字
//   - percall 两个：tottime/ncalls 与 cumtime/primitive
//   - pstats：strip_dirs、sort_stats(TIME/CUMULATIVE/CALLS)、print_stats 限制
//   - 指标用途：计数→bug/内联点；内部时间→热循环；累计时间→算法选型
package main

import (
	"fmt"
	"path/filepath"
	"sort"
	"strings"
)

const (
	call = iota
	ret
)

type event struct {
	kind int    // call / ret
	fn   string // ret 时空串 = 弹出当前栈顶（异常展开走返回语义）
	t    float64
}

type funcStat struct {
	ncalls    int
	primitive int
	tottime   float64
	cumtime   float64
	depth     int     // 当前栈上深度（判 primitive 用）
	primStart float64 // 当前 primitive 调用的进入时刻
}

// runEvents 确定性插桩引擎：事件间时长记给栈顶函数（tottime 的"不含子函数"口径）。
func runEvents(events []event) map[string]*funcStat {
	stats := map[string]*funcStat{}
	var stack []string
	last := 0.0
	for _, e := range events {
		switch e.kind {
		case call:
			if len(stack) > 0 {
				stats[stack[len(stack)-1]].tottime += e.t - last
			}
			last = e.t
			st := stats[e.fn]
			if st == nil {
				st = &funcStat{}
				stats[e.fn] = st
			}
			st.ncalls++
			if st.depth == 0 { // 首次进入：primitive 调用
				st.primitive++
				st.primStart = e.t
			}
			st.depth++
			stack = append(stack, e.fn)
		case ret:
			if len(stack) > 0 {
				stats[stack[len(stack)-1]].tottime += e.t - last
			}
			last = e.t
			fn := e.fn
			if fn == "" && len(stack) > 0 {
				fn = stack[len(stack)-1]
			}
			st := stats[fn]
			st.depth--
			stack = stack[:len(stack)-1]
			if st.depth == 0 { // 关闭 primitive 调用：计 cumtime
				st.cumtime += e.t - st.primStart
			}
		}
	}
	return stats
}

// ncallsStr：不递归只印单数字；递归印 total/primitive。
func ncallsStr(st *funcStat) string {
	if st.primitive == st.ncalls {
		return fmt.Sprintf("%d", st.ncalls)
	}
	return fmt.Sprintf("%d/%d", st.ncalls, st.primitive)
}

type key struct {
	file string
	line int
	fn   string
}

type row struct {
	k  key
	st *funcStat
}

// statsT 是 pstats.Stats 的最小复刻。
type statsT struct {
	rows []row
}

func newStats(m map[string]*funcStat, keys []key) *statsT {
	s := &statsT{}
	for _, k := range keys {
		s.rows = append(s.rows, row{k, m[k.fn]})
	}
	return s
}

// stripDirs 去掉文件名的路径前缀。
func (s *statsT) stripDirs() *statsT {
	for i := range s.rows {
		s.rows[i].k.file = filepath.Base(s.rows[i].k.file)
	}
	return s
}

// sortStats：TIME/CUMULATIVE/CALLS，同值按函数名字典序。
func (s *statsT) sortStats(sortKey string) *statsT {
	sort.SliceStable(s.rows, func(i, j int) bool {
		a, b := s.rows[i].st, s.rows[j].st
		var ka, kb float64
		switch sortKey {
		case "TIME":
			ka, kb = a.tottime, b.tottime
		case "CUMULATIVE":
			ka, kb = a.cumtime, b.cumtime
		case "CALLS":
			ka, kb = float64(a.ncalls), float64(b.ncalls)
		}
		if ka != kb {
			return ka > kb
		}
		return s.rows[i].k.fn < s.rows[j].k.fn
	})
	return s
}

func (s *statsT) format(rs []row) []string {
	var out []string
	for _, r := range rs {
		st := r.st
		per1, per2 := 0.0, 0.0
		if st.ncalls > 0 {
			per1 = st.tottime / float64(st.ncalls)
		}
		if st.primitive > 0 {
			per2 = st.cumtime / float64(st.primitive)
		}
		out = append(out, fmt.Sprintf("%s %g %g %g %g %s:%d(%s)",
			ncallsStr(st), st.tottime, per1, st.cumtime, per2, r.k.file, r.k.line, r.k.fn))
	}
	return out
}

func (s *statsT) printAll() []string  { return s.format(s.rows) }
func (s *statsT) limit(n int) []string { return s.format(s.rows[:min(n, len(s.rows))]) }
func (s *statsT) fraction(f float64) []string {
	n := int(f * float64(len(s.rows)))
	if n < 1 {
		n = 1
	}
	return s.limit(n)
}
func (s *statsT) substr(sub string) []string {
	var rs []row
	for _, r := range s.rows {
		if strings.Contains(r.k.fn, sub) {
			rs = append(rs, r)
		}
	}
	return s.format(rs)
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// usageHint：文档三句用途指引的映射。
func usageHint(st *funcStat) []string {
	var hints []string
	if st.ncalls >= 1000 {
		hints = append(hints, "inline-candidate")
	}
	if st.tottime > 10 {
		hints = append(hints, "hot-loop")
	}
	if st.cumtime > 50 {
		hints = append(hints, "algorithm-choice")
	}
	return hints
}

var case1 = []event{ // A 调 B 两次，B 各调 C 一次
	{call, "A", 0}, {call, "B", 2}, {call, "C", 5}, {ret, "C", 7}, {ret, "B", 9},
	{call, "B", 10}, {call, "C", 12}, {ret, "C", 13}, {ret, "B", 15}, {ret, "A", 18},
}

var case2 = []event{ // 递归：F 直接调 F
	{call, "F", 0}, {call, "F", 1}, {ret, "F", 2}, {ret, "F", 3},
}

func check(label string, cond bool) {
	if !cond {
		fmt.Println("FAIL:", label)
		panic("assertion failed: " + label)
	}
}

func names(rows []string) []string {
	var out []string
	for _, r := range rows {
		i := strings.LastIndex(r, "(")
		out = append(out, strings.TrimSuffix(r[i+1:], ")"))
	}
	return out
}

func main() {
	s := runEvents(case1)
	// 1. tottime：事件间隙记给执行中函数（不含子函数）
	check("tottime", s["A"].tottime == 6 && s["B"].tottime == 9 && s["C"].tottime == 3)
	// 2. cumtime：从调用到退出（含子函数）
	check("cumtime", s["A"].cumtime == 18 && s["B"].cumtime == 12 && s["C"].cumtime == 3)
	// 3. tottime 之和 == 总时长
	sum := 0.0
	for _, v := range s {
		sum += v.tottime
	}
	check("tottime sums to total", sum == 18.0)
	// 4. "不含子函数"的算术验证：B 的 cumtime - tottime == C 的 tottime
	check("excl subfunc", s["B"].cumtime-s["B"].tottime == s["C"].tottime)
	// 5. percall：tottime/ncalls 与 cumtime/primitive
	check("percall1", s["B"].tottime/float64(s["B"].ncalls) == 4.5)
	check("percall2", s["B"].cumtime/float64(s["B"].primitive) == 6.0)
	// 6. 无递归 → 单数字；有递归 → total/primitive
	check("ncalls str", ncallsStr(s["B"]) == "2" && ncallsStr(s["A"]) == "1")
	r := runEvents(case2)
	check("recursive 2/1", ncallsStr(r["F"]) == "2/1")
	// 7. 递归的 cumtime 特殊处理：只按 primitive 调用记账
	check("recursive cumtime", r["F"].tottime == 3 && r["F"].cumtime == 3 && r["F"].primitive == 1)

	// 8. pstats：sort TIME vs CUMULATIVE vs CALLS 顺序不同
	keys := []key{{"/app/pkg/a.py", 10, "A"}, {"/app/pkg/b.py", 20, "B"}, {"/app/pkg/c.py", 30, "C"}}
	ps := newStats(s, keys)
	check("sort TIME", eqStrs(names(ps.sortStats("TIME").printAll()), []string{"B", "A", "C"}))
	check("sort CUMULATIVE", eqStrs(names(ps.sortStats("CUMULATIVE").printAll()), []string{"A", "B", "C"}))
	check("sort CALLS", eqStrs(names(ps.sortStats("CALLS").printAll()), []string{"B", "C", "A"}))

	// 9. stripDirs 去路径
	ps2 := newStats(s, keys).stripDirs()
	check("strip_dirs", ps2.rows[0].k.file == "a.py")

	// 10. printStats 限制：int / float / 子串
	ps.sortStats("TIME")
	check("limit 2", len(ps.limit(2)) == 2)
	check("fraction 0.5", len(ps.fraction(0.5)) == 1)
	check("substr A", eqStrs(names(ps.substr("A")), []string{"A"}))
	rows := ps.printAll()
	check("row format", strings.HasPrefix(rows[0], "2 9 4.5 12 6 /app/pkg/b.py:20(B)"))

	// 11. 指标用途指引
	hot := &funcStat{ncalls: 5000, tottime: 20, cumtime: 80}
	check("hints hot", eqStrs(usageHint(hot), []string{"inline-candidate", "hot-loop", "algorithm-choice"}))
	warm := &funcStat{ncalls: 10, tottime: 1, cumtime: 2}
	check("hints warm", len(usageHint(warm)) == 0)

	// 12. 异常路径：ret 无函数名 → 弹出当前栈顶
	s3 := runEvents([]event{{call, "A", 0}, {call, "B", 1}, {ret, "", 4}, {ret, "A", 6}})
	check("exception unwind", s3["A"].tottime == 3 && s3["B"].tottime == 3 &&
		s3["B"].cumtime == 3 && s3["A"].cumtime == 6)

	fmt.Println("cprofile_stats: 12 组断言全部通过")
}

func eqStrs(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
