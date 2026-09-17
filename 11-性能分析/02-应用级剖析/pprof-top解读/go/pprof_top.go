// go tool pprof top 解读复刻：flat vs cum 聚合、栈深截断、-cum 排序、nodefraction 去噪。
//
// 口径（Go 官方博客 Profiling Go Programs，本轮实读）：
//   - CPU 剖析开启后，Go 程序每秒约 100 次停下并记录当前 goroutine 栈上的程序计数器
//   - top 前两列 = 该函数正在运行（非等待被调函数返回）的样本数与占比
//     第三列 = 列表内累计百分比；第四五列 = 该函数出现在栈上的样本数与占比（cum）
//   - 每个栈样本只含靠执行侧的 100 帧：递归深于 100 帧时根帧被截掉（博客 84.9% 实例）
//   - top 默认按 flat 排序；-cum 按累计列排序；--nodefraction=0.1 过滤小节点
package main

import (
	"fmt"
	"sort"
	"strings"
)

const maxStack = 100 // 每个栈样本保留的最大帧数（靠执行侧）

// truncate：stack 根在前、叶子在末尾；超长时保留叶子侧的 100 帧（根侧被截掉）。
func truncate(stack []string) []string {
	if len(stack) <= maxStack {
		return stack
	}
	return stack[len(stack)-maxStack:]
}

// stats：flat = 叶子帧样本数（正在运行）；cum = 出现在栈上任一位置的样本数。
type stats struct {
	flat  map[string]int
	cum   map[string]int
	total int
}

func aggregate(samples [][]string) stats {
	s := stats{flat: map[string]int{}, cum: map[string]int{}, total: len(samples)}
	for _, stack := range samples {
		t := truncate(stack)
		s.flat[t[len(t)-1]]++
		seen := map[string]bool{}
		for _, f := range t {
			seen[f] = true
		}
		for f := range seen {
			s.cum[f]++
		}
	}
	return s
}

func pct(x, total int) float64 {
	return 100.0 * float64(x) / float64(total)
}

// top 复刻 top 输出：默认按 flat 降序；byCum 按累计降序；nodefraction 按 cum 占比过滤。
// 同值按名字典序保证确定性（pprof 输出顺序稳定）。
func top(samples [][]string, n int, byCum bool, nodefraction float64) []string {
	s := aggregate(samples)
	var names []string
	for f := range s.cum {
		if pct(s.cum[f], s.total) >= nodefraction*100.0 {
			names = append(names, f)
		}
	}
	sort.Slice(names, func(i, j int) bool {
		ki, kj := s.flat[names[i]], s.flat[names[j]]
		if byCum {
			ki, kj = s.cum[names[i]], s.cum[names[j]]
		}
		if ki != kj {
			return ki > kj
		}
		return names[i] < names[j]
	})
	if len(names) > n {
		names = names[:n]
	}
	var rows []string
	running := 0.0
	for _, f := range names {
		fl := s.flat[f]
		running += pct(fl, s.total)
		rows = append(rows, fmt.Sprintf("%7d %5.1f%% %5.1f%% %8d %5.1f%% %s",
			fl, pct(fl, s.total), running, s.cum[f], pct(s.cum[f], s.total), f))
	}
	return rows
}

// durationEstimate：100 样本/秒（官方口径）→ 程序约运行 total/100 秒。
func durationEstimate(totalSamples int) float64 {
	return float64(totalSamples) / 100.0
}

// blogDataset 构造与博客同构的数据集（总量 1000）：
// 300 × main.main→FindLoops→mapaccess；200 × main.main→FindLoops；
// 100 × main.main→DFS；400 × main.main→DFS×120（深递归：截断后根侧 main.main 丢失）。
func blogDataset() [][]string {
	samples := make([][]string, 0, 1000)
	for i := 0; i < 300; i++ {
		samples = append(samples, []string{"main.main", "main.FindLoops", "runtime.mapaccess1_fast64"})
	}
	for i := 0; i < 200; i++ {
		samples = append(samples, []string{"main.main", "main.FindLoops"})
	}
	for i := 0; i < 100; i++ {
		samples = append(samples, []string{"main.main", "main.DFS"})
	}
	deep := append([]string{"main.main"}, makeDFS(120)...)
	for i := 0; i < 400; i++ {
		samples = append(samples, deep)
	}
	return samples
}

func makeDFS(n int) []string {
	out := make([]string, n)
	for i := range out {
		out[i] = "main.DFS"
	}
	return out
}

func check(label string, cond bool) {
	if !cond {
		fmt.Println("FAIL:", label)
		panic("assertion failed: " + label)
	}
}

func main() {
	samples := blogDataset()
	s := aggregate(samples)

	// 1. 总量与时长换算：2525 样本 ≈ 25.25 秒
	check("total 1000", s.total == 1000)
	check("2525 -> 25.25s", durationEstimate(2525) == 25.25)
	check("1000 -> 10s", durationEstimate(1000) == 10.0)

	// 2. flat：只记叶子帧
	check("flat mapaccess", s.flat["runtime.mapaccess1_fast64"] == 300)
	check("flat FindLoops", s.flat["main.FindLoops"] == 200)
	check("flat DFS", s.flat["main.DFS"] == 500)
	_, ok := s.flat["main.main"]
	check("root never leaf", !ok)

	// 3. cum：出现即计；深递归 400 个样本里 main.main 被截断丢失
	check("cum main.main 600", s.cum["main.main"] == 600)
	check("cum FindLoops 500", s.cum["main.FindLoops"] == 500)
	check("cum DFS 500", s.cum["main.DFS"] == 500)

	// 4. 截断语义
	deep := append([]string{"main.main"}, makeDFS(120)...)
	td := truncate(deep)
	check("deep keeps leaf side", len(td) == 100 && !contains(td, "main.main"))
	exact := append([]string{"A"}, makeB(99)...)
	check("exact 100 kept", len(truncate(exact)) == 100 && truncate(exact)[0] == "A")
	over := append([]string{"A", "B"}, makeC(100)...)
	to := truncate(over)
	check("102 drops A,B", len(to) == 100 && !contains(to, "A") && !contains(to, "B"))

	// 5. top 默认：按 flat 降序；running% 累计
	rows := top(samples, 10, false, 0)
	check("row0 DFS", strings.HasPrefix(rows[0], "    500  50.0%  50.0%      500  50.0% main.DFS"))
	check("row1 mapaccess", strings.HasPrefix(rows[1], "    300  30.0%  80.0%      300  30.0% runtime.mapaccess1_fast64"))
	check("row2 FindLoops", strings.HasPrefix(rows[2], "    200  20.0% 100.0%      500  50.0% main.FindLoops"))
	check("running accumulates", eqStrs(parts(rows, 2)[:3], []string{"50.0%", "80.0%", "100.0%"}))
	check("row3 flat0 root", strings.HasPrefix(rows[3], "      0   0.0% 100.0%") && strings.HasSuffix(rows[3], "main.main"))

	// 6. FindLoops 解读：flat 20% 但 cum 50%（自身或其调用在运行的比例）
	frow := strings.Fields(rows[2])
	check("flat 20 cum 50", frow[1] == "20.0%" && frow[4] == "50.0%")

	// 7. -cum：按累计列排序；根帧 flat=0 也能登顶
	rows = top(samples, 10, true, 0)
	check("cum row0 main.main", strings.HasPrefix(rows[0], "      0   0.0%   0.0%      600  60.0%") && strings.HasSuffix(rows[0], "main.main"))
	check("tie by name DFS first", strings.HasSuffix(rows[1], "main.DFS") && strings.HasSuffix(rows[2], "main.FindLoops"))
	check("cum last mapaccess", strings.HasSuffix(rows[3], "runtime.mapaccess1_fast64"))

	// 8. nodefraction 去噪：cum 占比 ≥ 阈值才保留
	rows = top(samples, 10, true, 0.5)
	check("nf 0.5", len(rows) == 3 && strings.HasSuffix(rows[0], "main.main"))
	rows = top(samples, 10, true, 0.51)
	check("nf 0.51", len(rows) == 1 && strings.HasSuffix(rows[0], "main.main"))

	// 9. topN 限制行数
	check("n=2", len(top(samples, 2, false, 0)) == 2)

	// 10. 无截断数据集：浅调用链 cum 应为 100%
	shallow := make([][]string, 40)
	for i := range shallow {
		shallow[i] = []string{"main.main", "main.work"}
	}
	s2 := aggregate(shallow)
	check("shallow full cum", s2.total == 40 && s2.cum["main.main"] == 40 && s2.cum["main.work"] == 40)

	fmt.Println("pprof_top: 10 组断言全部通过")
}

func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
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

func makeB(n int) []string {
	out := make([]string, n)
	for i := range out {
		out[i] = "B"
	}
	return out
}

func makeC(n int) []string {
	out := make([]string, n)
	for i := range out {
		out[i] = "C"
	}
	return out
}

func parts(rows []string, i int) []string {
	out := make([]string, 0, len(rows))
	for _, r := range rows {
		out = append(out, strings.Fields(r)[i])
	}
	return out
}
