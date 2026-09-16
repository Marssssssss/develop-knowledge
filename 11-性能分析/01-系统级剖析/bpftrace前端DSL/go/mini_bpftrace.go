// mini_bpftrace.go — bpftrace 语言前端核心的 Go 复刻
//
// 覆盖三件事(与 python/mini_bpftrace.py 同口径,输出逐字符对齐官方样例):
//  1. 探针说明符展开(短名 + * / ? 通配符)
//  2. hist()/lhist() 的分桶与 ASCII 渲染(标签列 15 + 计数列 9 + 柱区 52)
//  3. 事件驱动引擎:sys_enter + sys_exit 配对给 syscall 延迟打直方图
//
// 只有标准库。完整 DSL 词法/语法分析器见 python 版;Go 版把谓词/动作抽象成函数,
// 专注后端语义(桶边界、k/M/G 刻度、map 生命周期)。
//
// 运行: go run .
package main

import (
	"fmt"
	"math/bits"
	"sort"
	"strings"
)

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

// ---------------------------------------------------------------- 执行引擎

// Ctx 是一次事件的上下文(ctx.get 在 Python 版里对应这里的 Map/Set)。
type Ctx struct {
	fields map[string]int64
	str    map[string]string
}

// NewCtx 建一个空上下文。
func NewCtx() *Ctx { return &Ctx{fields: map[string]int64{}, str: map[string]string{}} }

// Block 对应 bpftrace 的一个 `probe /filter/ { action }`。
type Block struct {
	patterns []string
	pred     func(*Ctx) bool
	run      func(*Ctx)
}

// Engine 按事件顺序喂入,fed 的每个事件只匹配模式的块才执行。
type Engine struct {
	blocks []Block
	Aggs   map[string]*Agg
	Maps   map[string]int64
}

// NewEngine 建引擎。
func NewEngine(blocks ...Block) *Engine {
	return &Engine{blocks: blocks, Aggs: map[string]*Agg{}, Maps: map[string]int64{}}
}

// Feed 送入一个事件;probeName 用全名。
func (e *Engine) Feed(probeName string, ctx *Ctx) {
	for _, b := range e.blocks {
		hit := false
		for _, p := range b.patterns {
			if ProbeMatches(p, probeName) {
				hit = true
				break
			}
		}
		if !hit || (b.pred != nil && !b.pred(ctx)) {
			continue
		}
		b.run(ctx)
	}
}

// AggOrCreate 取或建某个 key 的聚合器。
func (e *Engine) AggOrCreate(key, kind string, first, lo, hi, step int64) *Agg {
	if a, ok := e.Aggs[key]; ok {
		return a
	}
	a := NewAgg(kind, first, lo, hi, step)
	e.Aggs[key] = a
	return a
}

// Report 按 key 字典序输出所有聚合结果。
func (e *Engine) Report() []string {
	keys := make([]string, 0, len(e.Aggs))
	for k := range e.Aggs {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := []string{}
	for _, k := range keys {
		lines := e.Aggs[k].Render()
		out = append(out, k+": "+strings.Join(lines, "\n"))
	}
	return out
}

// ---------------------------------------------------------------- 复刻教材里的一行式

// syscallLatencyBlocks 复刻:
//   tracepoint:syscalls:sys_enter_* /comm=="app"/ { @start[tid] = nsecs; }
//   tracepoint:syscalls:sys_exit_*  /comm=="app" && @start[tid]/
//       { @ns[comm] = hist(nsecs - @start[tid]); delete(@start, tid); }
func syscallLatencyBlocks(e *Engine) []Block {
	return []Block{
		{patterns: []string{ExpandProbe("t:syscalls:sys_enter_*")},
			pred: func(c *Ctx) bool { return c.str["comm"] == "app" },
			run: func(c *Ctx) {
				e.Maps[fmt.Sprintf("start:%d", c.fields["tid"])] = c.fields["nsecs"]
			}},
		{patterns: []string{ExpandProbe("t:syscalls:sys_exit_*")},
			pred: func(c *Ctx) bool {
				_, ok := e.Maps[fmt.Sprintf("start:%d", c.fields["tid"])]
				return c.str["comm"] == "app" && ok
			},
			run: func(c *Ctx) {
				k := fmt.Sprintf("start:%d", c.fields["tid"])
				delta := c.fields["nsecs"] - e.Maps[k]
				e.AggOrCreate("@ns["+c.str["comm"]+"]", "hist", delta, 0, 0, 0).Update(delta)
				delete(e.Maps, k) // 配对完成即释放,避免 map 无界增长
			}},
	}
}

func main() {
	fmt.Println("== 探针说明符 ==")
	for _, s := range []string{"k:f", "kr:vfs_read", "t:syscalls:sys_enter_read",
		"i:s:5", "BEGIN", "kernel:foo"} {
		fmt.Printf("  %-32s -> %s\n", s, ExpandProbe(s))
	}
	fmt.Println("== 通配符 ==")
	for _, tc := range [][2]string{
		{"tracepoint:sched:sched*", "tracepoint:sched:sched_wakeup"},
		{"kprobe:tcp_?", "kprobe:tcp_a"},
		{"kprobe:tcp_?", "kprobe:tcp_abc"},
	} {
		fmt.Printf("  %-28s ~ %-38s = %v\n", tc[0], tc[1], ProbeMatches(tc[0], tc[1]))
	}

	// 与 python 版同一组合成事件:8 条 4~14us 的小读 + 4 条 16~31ms 的大延迟 + 3 条写
	ev := []struct {
		name string
		d    int64
	}{{"read", 4000}, {"read", 5200}, {"read", 6800}, {"read", 8000}, {"read", 9500},
		{"read", 11000}, {"read", 13000}, {"read", 14500}, {"openat", 16000000},
		{"openat", 20000000}, {"openat", 24000000}, {"openat", 31000000},
		{"write", 700}, {"write", 1300}, {"write", 2600}}

	eng := &Engine{Aggs: map[string]*Agg{}, Maps: map[string]int64{}}
	eng.blocks = syscallLatencyBlocks(eng) // 闭包要引用同一个 Engine,故先建后挂
	ts := int64(0)
	for i, x := range ev {
		tid := int64(100 + i%2)
		ts += 1000
		enter := NewCtx()
		enter.fields["tid"], enter.fields["nsecs"] = tid, ts
		enter.str["comm"] = "app"
		eng.Feed(ExpandProbe("t:syscalls:sys_enter_"+x.name), enter)
		ts += x.d
		exit := NewCtx()
		exit.fields["tid"], exit.fields["nsecs"], exit.fields["ret"] = tid, ts, 1024
		exit.str["comm"] = "app"
		eng.Feed(ExpandProbe("t:syscalls:sys_exit_"+x.name), exit)
	}

	fmt.Println("== syscall 延迟直方图(@ns[app]) ==")
	for _, line := range eng.Report() {
		fmt.Println("  " + line)
	}
	fmt.Printf("== 配对完成后残留 @start 键 = %d 个 ==\n", len(eng.Maps))

	// 未配对的 exit:一个 comm 不符、一个 comm 相符但没有 @start[tid],都不应记账
	for _, comm := range []string{"other", "app"} {
		orphan := NewCtx()
		orphan.fields["tid"], orphan.fields["nsecs"] = 7, 5
		orphan.str["comm"] = comm
		eng.Feed(ExpandProbe("t:syscalls:sys_exit_read"), orphan)
	}
	fmt.Printf("  再喂 2 个孤儿 exit(comm=other / 缺 @start)后 agg 键数仍为 %d\n", len(eng.Aggs))

	fmt.Println("== lhist() 线性直方图(vfs_read 返回字节数) ==")
	lin := &Engine{Aggs: map[string]*Agg{}, Maps: map[string]int64{}}
	a := lin.AggOrCreate("@bytes[comm]", "lhist", 0, 0, 2000, 200)
	for _, v := range []int64{66, 120, 300, 500, 900, 1900, 2500, 99999} {
		a.Update(v)
	}
	for _, line := range lin.Report() {
		fmt.Println("  " + line)
	}
}
