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
	"sort"
	"strings"
)

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
