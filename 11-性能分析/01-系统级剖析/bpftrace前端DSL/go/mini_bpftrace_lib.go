package main

import (
	"fmt"
	"math/bits"
	"strings"
)

// mini_bpftrace_lib.go — 探针说明符展开、通配匹配、2 的幂与 lhist 分桶、
// 柱状图渲染、map 与聚合器。
//
// 与 mini_bpftrace.go 同属 package main,拆开只是为了让单文件落到 300 行以内;
// 执行引擎与 main 在 mini_bpftrace.go。

// ---------------------------------------------------------------- 探针说明符

var aliases = map[string]string{
	"t": "tracepoint", "k": "kprobe", "kr": "kretprobe", "u": "uprobe",
	"ur": "uretprobe", "p": "profile", "i": "interval", "s": "software",
	"h": "hardware", "w": "watchpoint", "it": "iter", "rt": "rawtracepoint",
	"f": "fentry", "fr": "fexit",
}

var builtinEvents = map[string]bool{
	"begin": true, "end": true, "BEGIN": true, "END": true,
}

// ExpandProbe 把短名探针展开成全名: k:f -> kprobe:f ; BEGIN -> begin。
func ExpandProbe(spec string) string {
	spec = strings.TrimSpace(spec)
	if builtinEvents[spec] {
		return strings.ToLower(spec)
	}
	head, rest, found := strings.Cut(spec, ":")
	if !found {
		return spec
	}
	if full, ok := aliases[head]; ok {
		return full + ":" + rest
	}
	return spec
}

// ProbeMatches 支持 `*`(任意串)与 `?`(单字符)的通配符匹配。
func ProbeMatches(pattern, actual string) bool {
	return globMatch([]rune(pattern), []rune(actual))
}

func globMatch(p, s []rune) bool {
	for len(p) > 0 {
		switch p[0] {
		case '*':
			for i := 0; i <= len(s); i++ {
				if globMatch(p[1:], s[i:]) {
					return true
				}
			}
			return false
		case '?':
			if len(s) == 0 {
				return false
			}
			p, s = p[1:], s[1:]
		default:
			if len(s) == 0 || p[0] != s[0] {
				return false
			}
			p, s = p[1:], s[1:]
		}
	}
	return len(s) == 0
}

// ---------------------------------------------------------------- 分桶与渲染

// HistIndex hist() 的桶号: 0/1 -> 0 ; 2..3 -> 1 ; 4..7 -> 2 ; 8..15 -> 3 …
func HistIndex(v int64) int {
	if v < 2 {
		return 0
	}
	return bits.Len64(uint64(v)) - 1
}

// FmtBucket 桶边界刻度: >=1024 起用 k/M/G(官方 tutorial 出现 [2k, 4k) / [512k, 1M))。
func FmtBucket(n int64) string {
	switch {
	case n >= 1<<30:
		return fmt.Sprintf("%dG", n>>30)
	case n >= 1<<20:
		return fmt.Sprintf("%dM", n>>20)
	case n >= 1<<10:
		return fmt.Sprintf("%dk", n>>10)
	}
	return fmt.Sprintf("%d", n)
}

// HistLabel 首桶是 [0, 1](同时收纳 0 与 1),其后左闭右开。
func HistLabel(i int) string {
	if i == 0 {
		return "[0, 1]"
	}
	return fmt.Sprintf("[%s, %s)", FmtBucket(1<<i), FmtBucket(1<<(i+1)))
}

const barWidth = 52

// RenderRow 渲染一行: 标签左对齐 15 列 + 计数右对齐 9 列 + " |" + 柱区 + "|"。
func RenderRow(label string, c, maxc int) string {
	bar := 0
	if c > 0 {
		bar = c * barWidth / maxc
		if bar == 0 {
			bar = 1
		}
	}
	return fmt.Sprintf("%-15s%9d |%s|", label, c, strings.Repeat("@", bar)+
		strings.Repeat(" ", barWidth-bar))
}

// RenderBuckets 只渲染到最后一个非空桶,中间空桶保留为 0 行。
func RenderBuckets(labels func(int) string, buckets map[int]int) []string {
	last, maxc := -1, 0
	for i, c := range buckets {
		if c > 0 && i > last {
			last = i
		}
		if c > maxc {
			maxc = c
		}
	}
	if last < 0 {
		return nil
	}
	first := 1 << 30
	for i := range buckets {
		if i < first {
			first = i
		}
	}
	out := []string{}
	for i := first; i <= last; i++ {
		out = append(out, RenderRow(labels(i), buckets[i], maxc))
	}
	return out
}

// ---------------------------------------------------------------- map 与聚合

// Agg 是 map 一个键的聚合状态。kind 取 count/sum/min/max/avg/stats/hist/lhist。
type Agg struct {
	kind    string
	n       int
	total   int64
	val     int64
	buckets map[int]int
	lo, hi  int64
	step    int64
}

// NewAgg 建聚合器;lhist 需要 (v, min, max, step) 四个参数。
func NewAgg(kind string, first int64, lo, hi, step int64) *Agg {
	a := &Agg{kind: kind, val: first, lo: lo, hi: hi, step: step}
	if kind == "hist" {
		a.buckets = map[int]int{0: 0}
	}
	if kind == "lhist" {
		a.buckets = map[int]int{}
		for k := int64(-1); k <= (hi-lo)/step; k++ {
			a.buckets[int(k)] = 0
		}
	}
	return a
}

// Update 累加一个样本。
func (a *Agg) Update(v int64) {
	switch a.kind {
	case "count":
		a.n++
	case "sum", "avg", "stats":
		a.n++
		a.total += v
	case "min":
		if v < a.val {
			a.val = v
		}
	case "max":
		if v > a.val {
			a.val = v
		}
	case "hist":
		a.buckets[HistIndex(v)]++
	case "lhist":
		a.n++
		a.buckets[LHistIndex(v, a.lo, a.hi, a.step)]++
	}
}

// Render 输出该聚合器的结果(hist/lhist 返回多行)。
func (a *Agg) Render() []string {
	switch a.kind {
	case "count":
		return []string{fmt.Sprint(a.n)}
	case "sum":
		return []string{fmt.Sprint(a.total)}
	case "min", "max":
		return []string{fmt.Sprint(a.val)}
	case "avg":
		if a.n == 0 {
			return []string{"0"}
		}
		return []string{fmt.Sprint(a.total / int64(a.n))}
	case "stats":
		avg := int64(0)
		if a.n > 0 {
			avg = a.total / int64(a.n)
		}
		return []string{fmt.Sprintf("count %d, average %d, total %d", a.n, avg, a.total)}
	case "hist":
		return RenderBuckets(HistLabel, a.buckets)
	case "lhist":
		return RenderBuckets(func(k int) string { return LHistLabel(k, a.lo, a.hi, a.step) }, a.buckets)
	}
	return nil
}

// LHistM 区间桶数 M = (max-min)/step。
func LHistM(lo, hi, step int64) int64 { return (hi - lo) / step }

// LHistIndex (-inf, lo] -> -1 ; [lo, hi) -> 0..M-1 ; [hi, +inf) -> M。
func LHistIndex(v, lo, hi, step int64) int {
	switch {
	case v <= lo:
		return -1
	case v >= hi:
		return int(LHistM(lo, hi, step))
	}
	return int((v - lo) / step)
}

// LHistLabel 标签与官方文档一致: (...,0] 与 [2000,...)。
func LHistLabel(k int, lo, hi, step int64) string {
	if k == -1 {
		return fmt.Sprintf("(...,%d]", lo)
	}
	if int64(k) == LHistM(lo, hi, step) {
		return fmt.Sprintf("[%d,...)", hi)
	}
	return fmt.Sprintf("[%d, %d)", lo+int64(k)*step, lo+int64(k+1)*step)
}

