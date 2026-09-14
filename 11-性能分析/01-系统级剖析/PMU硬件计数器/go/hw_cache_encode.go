// hw_cache_encode.go — PMU 事件编码矩阵与派生指标(Go 版,教学用)
//
// 覆盖 man7 perf_event_open(2) 里最容易记错的三块:
//   1) PERF_TYPE_HW_CACHE 的 config 编码:config = id | (op<<8) | (result<<16)
//   2) 通用硬件事件(PERF_TYPE_HARDWARE)与内核合成事件(PERF_TYPE_SOFTWARE)常量表
//   3) read_format 位掩码如何决定 read(2) 返回结构的布局;以及缩放后怎么算 IPC
//
// 运行: go run .
package main

import "fmt"

type named struct {
	name string
	val  uint64
	desc string
}

var cacheIDs = []named{
	{"L1D", 0, "一级数据缓存"},
	{"L1I", 1, "一级指令缓存"},
	{"LL", 2, "末级缓存(Last-Level,通常即 L3/LLC)"},
	{"DTLB", 3, "数据 TLB"},
	{"ITLB", 4, "指令 TLB"},
	{"BPU", 5, "分支预测单元"},
	{"NODE", 6, "本地内存访问(3.1+)"},
}

var cacheOps = []named{
	{"READ", 0, "读访问"},
	{"WRITE", 1, "写访问"},
	{"PREFETCH", 2, "预取访问"},
}

var cacheResults = []named{
	{"ACCESS", 0, "测量访问次数"},
	{"MISS", 1, "测量未命中次数"},
}

var hwEvents = []named{
	{"CPU_CYCLES", 0, "总周期(受 CPU 频率缩放影响)"},
	{"INSTRUCTIONS", 1, "已退休指令"},
	{"CACHE_REFERENCES", 2, "缓存访问(通常指 LLC 访问,可能含预取与一致性消息)"},
	{"CACHE_MISSES", 3, "缓存未命中(通常指 LLC 未命中)"},
	{"BRANCH_INSTRUCTIONS", 4, "已退休分支指令"},
	{"BRANCH_MISSES", 5, "误预测分支"},
	{"BUS_CYCLES", 6, "总线周期(可能不同于总周期)"},
	{"STALLED_CYCLES_FRONTEND", 7, "发射阶段停滞周期(3.0+)"},
	{"STALLED_CYCLES_BACKEND", 8, "退休阶段停滞周期(3.0+)"},
	{"REF_CPU_CYCLES", 9, "总周期,不受频率缩放影响(3.3+)"},
}

var swEvents = []named{
	{"CPU_CLOCK", 0, "高分辨率 per-CPU 定时器"},
	{"TASK_CLOCK", 1, "运行任务特定的时钟"},
	{"PAGE_FAULTS", 2, "缺页次数(总)"},
	{"CONTEXT_SWITCHES", 3, "上下文切换(2.6.34 起记在内核态)"},
	{"CPU_MIGRATIONS", 4, "进程迁移到新 CPU 的次数"},
	{"PAGE_FAULTS_MIN", 5, "次缺页:无需磁盘 I/O"},
	{"PAGE_FAULTS_MAJ", 6, "主缺页:需要磁盘 I/O"},
	{"ALIGNMENT_FAULTS", 7, "对齐错误(x86 从不发生)"},
	{"EMULATION_FAULTS", 8, "内核陷阱并模拟未实现指令"},
	{"DUMMY", 9, "不计数,仅用于关联 mmap/comm 记录(3.12+)"},
	{"BPF_OUTPUT", 10, "从 BPF 生成原始采样(4.4+)"},
	{"CGROUP_SWITCHES", 11, "切换到不同 cgroup 的切换次数(5.13+)"},
}

var readFormatFlags = []named{
	{"PERF_FORMAT_TOTAL_TIME_ENABLED", 1 << 0, "追加 u64 time_enabled(缩放必需)"},
	{"PERF_FORMAT_TOTAL_TIME_RUNNING", 1 << 1, "追加 u64 time_running(缩放必需)"},
	{"PERF_FORMAT_ID", 1 << 2, "每个事件追加 u64 唯一 id"},
	{"PERF_FORMAT_GROUP", 1 << 3, "允许一次 read 读完整组"},
	{"PERF_FORMAT_LOST", 1 << 4, "追加 u64 丢失采样数(6.0+,需采样事件)"},
}

var attrSizes = []named{
	{"PERF_ATTR_SIZE_VER0", 64, "首个发布的结构体"},
	{"PERF_ATTR_SIZE_VER1", 72, "2.6.33 加入断点"},
	{"PERF_ATTR_SIZE_VER2", 80, "3.4 加入分支采样"},
	{"PERF_ATTR_SIZE_VER3", 96, "3.7 加入 sample_regs_user / sample_stack_user"},
	{"PERF_ATTR_SIZE_VER4", 104, "3.19 加入 sample_regs_intr"},
	{"PERF_ATTR_SIZE_VER5", 112, "4.1 加入 aux_watermark"},
}

// encodeCacheConfig 就是 man page 给出的那条公式
func encodeCacheConfig(id, op, result uint64) uint64 {
	return id | (op << 8) | (result << 16)
}

func decodeCacheConfig(cfg uint64) (string, bool) {
	id, op, res := cfg&0xff, (cfg>>8)&0xff, (cfg>>16)&0xff
	if id >= uint64(len(cacheIDs)) || op >= uint64(len(cacheOps)) || res >= uint64(len(cacheResults)) {
		return "", false
	}
	if cfg>>24 != 0 {
		return "", false // 高 8 位应保留为 0
	}
	return fmt.Sprintf("%s %s %s", cacheIDs[id].name, cacheOps[op].name, cacheResults[res].name), true
}

func printMatrix() {
	fmt.Println("=== PERF_TYPE_HW_CACHE 编码矩阵 config = id | (op<<8) | (result<<16) ===")
	fmt.Printf("  %-6s %-9s %-7s %-14s %s\n", "id", "op", "result", "config(hex)", "解码验证")
	for _, id := range cacheIDs {
		for _, op := range cacheOps {
			for _, res := range cacheResults {
				cfg := encodeCacheConfig(id.val, op.val, res.val)
				dec, ok := decodeCacheConfig(cfg)
				status := "OK"
				if !ok || dec == "" {
					status = "!!"
				}
				fmt.Printf("  %-6s %-9s %-7s 0x%08x     %s\n",
					id.name, op.name, res.name, cfg, status)
			}
		}
	}
	fmt.Printf("  共 %d 种组合;所谓\"未命中率\"= 同 id+op 的 MISS / ACCESS\n",
		len(cacheIDs)*len(cacheOps)*len(cacheResults))
}

func printTables() {
	fmt.Println("\n=== PERF_TYPE_HARDWARE(通用硬件事件)===")
	for _, e := range hwEvents {
		fmt.Printf("  config=%-2d %-24s %s\n", e.val, e.name, e.desc)
	}
	fmt.Println("\n=== PERF_TYPE_SOFTWARE(内核合成事件)===")
	for _, e := range swEvents {
		fmt.Printf("  config=%-2d %-20s %s\n", e.val, e.name, e.desc)
	}
	fmt.Println("\n=== struct perf_event_attr.size 的版本语义(向前/后兼容)===")
	for _, s := range attrSizes {
		fmt.Printf("  %-22s = %3d  %s\n", s.name, s.val, s.desc)
	}
}

func printReadFormat(mask uint64) {
	fields := 1 // nr
	fmt.Printf("\n=== read_format 解码 (0x%x) ===\n", mask)
	for _, f := range readFormatFlags {
		if mask&f.val != 0 {
			fmt.Printf("  [x] %-34s %s\n", f.name, f.desc)
			fields++
		} else {
			fmt.Printf("  [ ] %-34s %s\n", f.name, f.desc)
		}
	}
	perEvent := 0
	if mask&(1<<3) != 0 { // GROUP:每个事件只多一个 value(+可选的 id/lost)
		perEvent = 1
		if mask&(1<<2) != 0 {
			perEvent++
		}
		if mask&(1<<4) != 0 {
			perEvent++
		}
	}
	fmt.Printf("  -> 布局:开头 %d 个 u64,之后每个事件 %d 个 u64;缓冲区不足 read 返回 ENOSPC\n",
		fields, perEvent)
}

// derive 演示:缩放后再算比率,并把多路复用比一并报出来
func derive(name string, value, enabled, running uint64) (uint64, float64) {
	if running == 0 {
		return 0, 0
	}
	quot, rem := value/running, value%running
	scaled := quot*enabled + (rem*enabled)/running
	return scaled, float64(enabled) / float64(running)
}

func main() {
	printMatrix()
	printTables()
	printReadFormat(1<<3 | 1<<0 | 1<<1) // GROUP | TOTAL_TIME_ENABLED | TOTAL_TIME_RUNNING

	fmt.Println("\n=== 阅读结果:缩放必须做在\"比率之前\" ===")
	// 同一组内的三个事件:前两个没被复用,第三个被复用了 4 倍
	type sample struct {
		name           string
		value, enabled uint64
		running        uint64
	}
	samples := []sample{
		{"INSTRUCTIONS", 418200, 100000, 100000},
		{"CPU_CYCLES", 597000, 100000, 100000},
		{"CACHE_MISSES", 12745, 100000, 25000},
	}
	raw := map[string]float64{}
	for _, s := range samples {
		sv, ratio := derive(s.name, s.value, s.enabled, s.running)
		raw[s.name] = float64(s.value)
		reused := ""
		if ratio > 1.0 {
			reused = "  <- 多路复用!"
		}
		fmt.Printf("  %-15s raw=%-10d scaled=%-10d 缩放比=%.2f%s\n",
			s.name, s.value, sv, ratio, reused)
	}
	fmt.Printf("  不缩放就算 IPC(错): %.3f\n", raw["INSTRUCTIONS"]/raw["CPU_CYCLES"])
	scaled := map[string]float64{}
	for _, s := range samples {
		v, _ := derive(s.name, s.value, s.enabled, s.running)
		scaled[s.name] = float64(v)
	}
	fmt.Printf("  先缩放再算 IPC(对): %.3f\n", scaled["INSTRUCTIONS"]/scaled["CPU_CYCLES"])
	fmt.Println("  结论:事件数超过槽位时,原始 read 值来自不同时间窗口,直接做比会失真。")
}
