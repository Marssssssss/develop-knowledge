// dynamic_ftrace.go — dynamic ftrace 的补丁模型 + 过滤器优先级 + 函数 profiler(Go 版,教学用)
//
// 模型要点(全部对应 docs.kernel.org/trace/ftrace.html):
//   * 编译期每个可跟踪函数入口埋一条 5 字节 nop(原 mcount/__fentry__ 调用点)
//   * 启用时做 text patching:把 nop 改写成相对 call 到 trampoline;关掉 tracer 时改回 nop
//   * set_ftrace_filter / set_ftrace_notrace 决定改写哪些调用点;两者同时命中时不跟踪
//   * set_graph_function / set_graph_notrace 决定 function_graph 展开哪棵子树
//   * dyn_ftrace_total_info = 当前仍处于 nop 状态的函数数量
//   * function_profile_enabled -> trace_stat/function<cpu> 是"调用次数 + 耗时"直方图
//
// 运行: go run .
package main

import (
	"fmt"
	"math/rand"
	"sort"
	"strings"
)

const (
	nopPatch  = "0f 1f 44 00 00" // 5 字节 nop:函数入口的编译期占位
	callPatch = "e8 rel32"       // 5 字节相对 call:指向 trampoline
)

// CallSite 是一个被编译进来的函数入口调用点
type CallSite struct {
	Fn      string
	Addr    uint64
	Patched bool // true = 已改写为 call(正在被跟踪);false = 仍是 nop
	Hits    uint64
	Nanos   uint64
}

type ftrace struct {
	sites    []*CallSite
	tracerOn bool            // 是否选了 tracer(对应 current_tracer != nop)
	filter   map[string]bool // set_ftrace_filter(空 = 全部)
	notrace  map[string]bool // set_ftrace_notrace(优先级更高)
	graph    map[string]bool // set_graph_function
	graphNo  map[string]bool // set_graph_notrace
}

func newFtrace(funcs []string) *ftrace {
	f := &ftrace{filter: map[string]bool{}, notrace: map[string]bool{},
		graph: map[string]bool{}, graphNo: map[string]bool{}}
	for i, fn := range funcs {
		f.sites = append(f.sites, &CallSite{Fn: fn, Addr: 0xffffffff81000000 + uint64(i)*0x30})
	}
	return f
}

// wanted:filter 与 notrace 同时命中时不跟踪(notrace 优先),这是 ftrace 的明文语义
func (f *ftrace) wanted(fn string) bool {
	if !f.tracerOn {
		return false // current_tracer = nop
	}
	if f.notrace[fn] {
		return false
	}
	if len(f.filter) == 0 {
		return true // 空 filter = 所有函数都可跟踪
	}
	return f.filter[fn]
}

// applyPatches 执行 text patching,返回 (改写为 call 的数量, 仍为 nop 的数量)
func (f *ftrace) applyPatches() (enabled, nopTotal int) {
	for _, s := range f.sites {
		on := f.wanted(s.Fn)
		if on != s.Patched { // 只有状态变化才真的改机器码
			from, to := nopPatch, callPatch
			if !on {
				from, to = callPatch, nopPatch
			}
			fmt.Printf("    patch %#x %-22s %s -> %s\n", s.Addr, s.Fn, from, to)
			s.Patched = on
		}
		if on {
			enabled++
		} else {
			nopTotal++
		}
	}
	return enabled, nopTotal
}

// graphTraced:function_graph 只在 set_graph_function 里展开;graph_notrace 命中即停
func (f *ftrace) graphTraced(fn string) bool {
	if f.graphNo[fn] || !f.tracerOn {
		return false
	}
	if len(f.graph) == 0 {
		return f.wanted(fn)
	}
	return f.graph[fn]
}

// call 模拟一次函数调用:只有被 patch 的位点会进 trampoline 记账
func (f *ftrace) call(fn string, ns uint64) {
	for _, s := range f.sites {
		if s.Fn == fn && s.Patched {
			s.Hits++
			s.Nanos += ns
			return
		}
	}
}

// profilerReport 对应 trace_stat/function<cpu> 的直方图
func (f *ftrace) profilerReport(top int) {
	type row struct {
		fn    string
		hits  uint64
		nanos uint64
	}
	var rows []row
	for _, s := range f.sites {
		if s.Hits > 0 {
			rows = append(rows, row{s.Fn, s.Hits, s.Nanos})
		}
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].nanos > rows[j].nanos })
	fmt.Println("  # trace_stat/function0  (function_profile_enabled=1)")
	fmt.Println("    Function                            Hit     ns(avg)")
	for i, r := range rows {
		if i >= top {
			break
		}
		fmt.Printf("    %-32s %6d  %9.1f\n", r.fn, r.Hits, float64(r.nanos)/float64(r.Hits))
	}
	if len(rows) == 0 {
		fmt.Println("    (无样本:filter 把要看的函数排除了,或压根没被调用)")
	}
}

func filterList(m map[string]bool) string {
	if len(m) == 0 {
		return "(空)"
	}
	var ks []string
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return strings.Join(ks, ", ")
}

func main() {
	funcs := []string{"do_sys_open", "vfs_read", "ext4_file_write_iter", "tcp_sendmsg",
		"kmem_cache_alloc", "finish_task_switch", "__schedule", "netif_receive_skb"}
	f := newFtrace(funcs)

	fmt.Println("=== 0) 初始 current_tracer=nop:所有调用点都是 5 字节 nop(开销≈0)===")
	for _, s := range f.sites[:3] {
		fmt.Printf("    %-24s addr=%#x bytes=%s\n", s.Fn, s.Addr, nopPatch)
	}
	fmt.Printf("    ... 共 %d 个位点\n", len(f.sites))

	fmt.Println("\n=== 1) 选 function tracer + set_ftrace_filter={do_sys_open, vfs_read, kmem_cache_alloc} ===")
	f.tracerOn = true
	for _, fn := range []string{"do_sys_open", "vfs_read", "kmem_cache_alloc"} {
		f.filter[fn] = true
	}
	fmt.Println("    set_ftrace_filter  =", filterList(f.filter))
	en, nopTot := f.applyPatches()
	fmt.Printf("    -> 改为 call 的位点 %d 个,仍为 nop %d 个\n", en, nopTot)
	fmt.Printf("    -> dyn_ftrace_total_info(仍为 nop 的函数数)= %d\n", nopTot)

	fmt.Println("\n=== 2) 再加 set_ftrace_notrace={vfs_read}:同时命中时不跟踪 ===")
	f.notrace["vfs_read"] = true
	en, nopTot = f.applyPatches()
	fmt.Println("    set_ftrace_notrace =", filterList(f.notrace))
	fmt.Printf("    -> vfs_read 被排除:改为 call 的位点 %d 个,dyn_ftrace_total_info = %d\n", en, nopTot)

	fmt.Println("\n=== 3) function_graph 收窄:set_graph_function={do_sys_open} ===")
	f.graph["do_sys_open"] = true
	fmt.Println("    G = function_graph 展开子树;f = 只被 function tracer 记一笔")
	for _, fn := range funcs {
		mark := "  "
		if f.graphTraced(fn) {
			mark = "G "
		} else if f.wanted(fn) {
			mark = "f "
		}
		fmt.Printf("    %s%-24s\n", mark, fn)
	}

	fmt.Println("\n=== 4) 跑 20000 轮调用,统计直方图(只有被 patch 的位点记账)===")
	rng := rand.New(rand.NewSource(42))
	weights := map[string]int{"do_sys_open": 40, "kmem_cache_alloc": 35,
		"vfs_read": 15, "tcp_sendmsg": 10}
	for i := 0; i < 20000; i++ {
		for fn, w := range weights {
			if rng.Intn(100) < w {
				f.call(fn, uint64(200+rng.Intn(3000)))
			}
		}
	}
	f.profilerReport(8)

	fmt.Println("\n=== 5) 收尾:echo nop > current_tracer(把 call 全部还原成 nop)===")
	f.tracerOn = false
	f.applyPatches()
	stillCall := 0
	for _, s := range f.sites {
		if s.Patched {
			stillCall++
		}
	}
	fmt.Printf("    仍处于 call 状态的位点 = %d(应为 0)\n", stillCall)
	fmt.Println("    注意:改 current_tracer 会清空 ring buffer 与 snapshot buffer。")
}
