package main

import (
	"strconv"
	"strings"
)

// irq_parse.go — /proc/interrupts、/proc/softirqs、/proc/net/softnet_stat 的解析器,
// 以及「累计值求差」与「%si 高」的归因判定。
//
// 与 irq_check.go 同属 package main,拆开只是为了让单文件落到 300 行以内;
// Go 同包多文件是零语义变化的搬运。IRQ 亲和性在 irq_affinity.go,自检在 irq_check.go。

// softirq 类型(kernel 预定义,顺序与 /proc/softirqs、/proc/stat 的 softirq 行一致)
var softirqNames = []string{"HI", "TIMER", "NET_TX", "NET_RX", "BLOCK",
	"IRQ_POLL", "TASKLET", "SCHED", "HRTIMER", "RCU"}

const netdevBudgetDefault = 300 // net.core.netdev_budget 的内核默认值

// 归因结论
const (
	realWork     = "REAL_WORK"     // 高 %si 是真活,不是排队问题
	raiseBacklog = "RAISE_BACKLOG" // 队列太浅,包被丢在 backlog 里
	raiseBudget  = "RAISE_BUDGET"  // 单次预算用完还有活,被 time_squeeze 打断
)

// ---------------------------------------------------------------- /proc/interrupts

// IrqRow 一条中断记录。Label 有编号时是 IRQ 号,否则是架构向量名(NMI/LOC/RES …)。
type IrqRow struct {
	Label  string
	Counts []int
	Rest   []string
}

// Device 最后一个 token 是驱动/设备名(共享中断时是逗号连接的驱动列表)。
func (r IrqRow) Device() string {
	if len(r.Rest) == 0 {
		return ""
	}
	return r.Rest[len(r.Rest)-1]
}

// IsNumbered 判断「是不是 IRQ」的唯一依据是标签能不能当整数读 —— 不用白名单,
// 因为架构向量的种类随内核版本和平台变化。
func (r IrqRow) IsNumbered() bool {
	if r.Label == "" {
		return false
	}
	_, err := strconv.Atoi(r.Label)
	return err == nil
}

// ParseInterrupts 返回 (CPU 列名, 所有数据行)。
// 紧跟标签的前 len(cpuCols) 个整数才是每核计数,后面的 token 是控制器类型与驱动名。
func ParseInterrupts(text string) ([]string, []IrqRow) {
	var cpuCols []string
	var rows []IrqRow
	for _, line := range strings.Split(text, "\n") {
		tok := strings.Fields(line)
		if len(tok) == 0 {
			continue
		}
		if len(cpuCols) == 0 && allPrefix(tok, "CPU") {
			cpuCols = tok
			continue
		}
		if !strings.HasSuffix(tok[0], ":") {
			continue
		}
		row := IrqRow{Label: strings.TrimSuffix(tok[0], ":")}
		n := 0
		for n < len(cpuCols) && 1+n < len(tok) {
			v, err := strconv.Atoi(tok[1+n])
			if err != nil {
				break
			}
			row.Counts = append(row.Counts, v)
			n++
		}
		row.Rest = tok[1+n:]
		rows = append(rows, row)
	}
	return cpuCols, rows
}

func allPrefix(tok []string, p string) bool {
	for _, t := range tok {
		if !strings.HasPrefix(t, p) {
			return false
		}
	}
	return len(tok) > 0
}

// ---------------------------------------------------------------- /proc/softirqs

// ParseSoftirqs 名字从内核预定义表里取,不靠行内猜。返回 (CPU 列名, 名->每核计数)。
func ParseSoftirqs(text string) ([]string, map[string][]int) {
	var cpuCols []string
	out := map[string][]int{}
	for _, line := range strings.Split(text, "\n") {
		tok := strings.Fields(strings.ReplaceAll(line, ":", " "))
		if len(tok) == 0 {
			continue
		}
		if len(cpuCols) == 0 && allPrefix(tok, "CPU") {
			cpuCols = tok
			continue
		}
		if len(tok) < 1+len(cpuCols) {
			continue
		}
		vals := make([]int, len(cpuCols))
		ok := true
		for i := 0; i < len(cpuCols); i++ {
			v, err := strconv.Atoi(tok[1+i])
			if err != nil {
				ok = false
				break
			}
			vals[i] = v
		}
		if ok {
			out[tok[0]] = vals
		}
	}
	return cpuCols, out
}

// DiffSeries 累计值只能求差。求和到「全系统总量」再差分,避免逐核相加时错位。
func DiffSeries(before, after map[string][]int) map[string]int {
	out := map[string]int{}
	for k := range before {
		out[k] = sum(after[k]) - sum(before[k])
	}
	for k := range after {
		if _, ok := out[k]; !ok {
			out[k] = sum(after[k]) - sum(before[k])
		}
	}
	return out
}

func sum(xs []int) int {
	t := 0
	for _, x := range xs {
		t += x
	}
	return t
}

// ---------------------------------------------------------------- /proc/net/softnet_stat

// ParseSoftnetStat 每 CPU 一行,全部是十六进制,且文件里没有表头。
// 列序必须靠外部知识:1=处理过的包数 2=因 backlog 满而丢的包数 3=time_squeeze。
func ParseSoftnetStat(text string) [][]int {
	var out [][]int
	for _, line := range strings.Split(text, "\n") {
		tok := strings.Fields(line)
		if len(tok) == 0 {
			continue
		}
		row := make([]int, 0, len(tok))
		for _, t := range tok {
			v, err := strconv.ParseInt(t, 16, 64)
			if err != nil {
				break
			}
			row = append(row, int(v))
		}
		if len(row) > 0 {
			out = append(out, row)
		}
	}
	return out
}

// SoftnetTotals (处理总数, 丢包总数, squeeze 总数),短行按缺列补 0。
func SoftnetTotals(rows [][]int) (int, int, int) {
	var p, d, s int
	for _, r := range rows {
		for i := 0; i < 3; i++ {
			if i < len(r) {
				switch i {
				case 0:
					p += r[i]
				case 1:
					d += r[i]
				case 2:
					s += r[i]
				}
			}
		}
	}
	return p, d, s
}

// Attribute 把「%si 高」拆成三种完全不同的动作。丢包优先于 squeeze:
// 丢包是已经发生的损失,squeeze 只是「被打断」,前者更紧急。
func Attribute(siShare float64, dropped, squeeze int) string {
	if dropped <= 0 && squeeze <= 0 {
		return realWork
	}
	if dropped > squeeze {
		return raiseBacklog
	}
	return raiseBudget
}

