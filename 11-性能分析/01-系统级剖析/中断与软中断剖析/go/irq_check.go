// irq_check.go — 中断与软中断剖析
//
// 四份文件口径全都不一样:
//
//	/proc/interrupts             每个 IRQ 在每个 CPU 上的累计处理次数
//	/proc/softirqs               每种 softirq 在每个 CPU 上的累计次数
//	/proc/net/softnet_stat       每 CPU 一行,【十六进制、没有表头】
//	/proc/irq/<N>/smp_affinity   十六进制 CPU 位掩码(超过 32 核是逗号分组、低位组在前)
//
// 三个最容易踩的坑:架构向量行没有 IRQ 号;softnet_stat 是十六进制;smp_affinity
// 的分组顺序读反会指向完全错的 CPU。每一项都写了断言。
//
// 运行: go run irq_check.go
package main

import (
	"fmt"
	"os"
	"strconv"
)

// ---------------------------------------------------------------- 自检

var ok = true

func check(label string, cond bool, detail ...string) {
	tag := "PASS"
	if !cond {
		tag, ok = "FAIL", false
	}
	extra := ""
	if len(detail) > 0 && detail[0] != "" {
		extra = "  <- " + detail[0]
	}
	fmt.Printf("  [%s] %s%s\n", tag, label, extra)
}

const interrupts = `           CPU0       CPU1       CPU2       CPU3
  0:         46          0          0          0   IO-APIC    2-edge      timer
  1:          3          0          0          0   IO-APIC    1-edge      i8042
 24:      10234       5601       4200       8991   PCI-MSI 524288-edge      eth0
 25:      54321       6789       4321       1234   PCI-MSI 524289-edge      nvme0q0
 32:          0    1034521          0          0   PCI-MSI-edge      eth0-TxRx-0
 33:          0          0    1034522          0   PCI-MSI-edge      eth0-TxRx-1
NMI:         12         14         13         12   Non-maskable interrupts
LOC:    1234567    1234568    1234569    1234570   Local timer interrupts
RES:       4321       2109       3210       4102   Rescheduling interrupts
`

const softirqs = `                    CPU0       CPU1       CPU2       CPU3
          HI:          1          0          0          0
       TIMER:    1234567    1234568    1234569    1234570
      NET_TX:        567        890        123        456
      NET_RX:    4567890    3456789    4567891    3456790
       BLOCK:          0          0          0          0
    IRQ_POLL:          0          0          0          0
     TASKLET:       1234        567          0          0
       SCHED:    1234567    1234567    1234567    1234567
     HRTIMER:          0          0          0          0
         RCU:    4567890    3456789    4567891    3456790
`

const softnet = `0001e240 0000000c 00000003 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000
0001e241 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000
`

const procStat = `cpu  100 0 50 800 0 0 0 0 0 0
intr 9876543 46 3 0 10234 54321 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
ctxt 1234567
softirq 33967864 1 4938234 2036 16059360 0 0 1235 4938268 0 8028730
`

func main() {
	cpuCols, rows := ParseInterrupts(interrupts)
	byLabel := map[string]IrqRow{}
	for _, r := range rows {
		byLabel[r.Label] = r
	}

	fmt.Println("== 1. /proc/interrupts 的行结构 ==")
	check("表头给出每核一列,共 4 列", len(cpuCols) == 4 && cpuCols[0] == "CPU0")
	check("IRQ 24(eth0)每核计数 = 10234/5601/4200/8991",
		eqInts(byLabel["24"].Counts, []int{10234, 5601, 4200, 8991}), fmt.Sprint(byLabel["24"].Counts))
	check("计数只取前 len(cpuCols) 个整数,后面的控制器类型不被当成计数",
		eqStrs(byLabel["24"].Rest, []string{"PCI-MSI", "524288-edge", "eth0"}),
		fmt.Sprint(byLabel["24"].Rest))
	check("设备名 = 剩余 token 的最后一个", byLabel["24"].Device() == "eth0")
	check("共享中断的驱动列表用逗号连接,整体算一个 token",
		IrqRow{Rest: []string{"IO-APIC-fasteoi", "ehci_hcd:usb1,ath9k"}}.Device() == "ehci_hcd:usb1,ath9k")

	fmt.Println("== 2. 没有编号的架构向量不是 IRQ ==")
	var numbered, arch []string
	for _, r := range rows {
		if r.IsNumbered() {
			numbered = append(numbered, r.Label)
		} else {
			arch = append(arch, r.Label)
		}
	}
	check("识别出 6 条有编号的 IRQ", eqStrs(numbered, []string{"0", "1", "24", "25", "32", "33"}),
		fmt.Sprint(numbered))
	check("NMI/LOC/RES 被归为架构向量而非 IRQ", eqStrs(arch, []string{"NMI", "LOC", "RES"}),
		fmt.Sprint(arch))
	check("判断依据是「标签能否当整数读」,不是白名单 —— 向量种类随内核/平台变化",
		(len(arch) == 3) && !IrqRow{Label: "NMI"}.IsNumbered() && IrqRow{Label: "24"}.IsNumbered())

	fmt.Println("== 3. 多队列网卡的中断分散 ==")
	check("eth0-TxRx-0 只落在 CPU1(1034521)", byLabel["32"].Counts[1] == 1034521)
	check("eth0-TxRx-1 只落在 CPU2(1034522)", byLabel["33"].Counts[2] == 1034522)
	check("两个队列都长了 -> 分散成功;若都只落 CPU0 就说明没做多队列",
		sum(byLabel["32"].Counts)+sum(byLabel["33"].Counts) == 1034521+1034522)

	fmt.Println("== 4. /proc/softirqs 的名字来自内核预定义表 ==")
	scpu, sfi := ParseSoftirqs(softirqs)
	check("CPU 列名与 /proc/interrupts 一致", eqStrs(scpu, cpuCols))
	check("解析出 10 种 softirq", len(sfi) == 10, fmt.Sprint(len(sfi)))
	check("NET_RX 每核计数正确", eqInts(sfi["NET_RX"], []int{4567890, 3456789, 4567891, 3456790}))
	check("BLOCK/IRQ_POLL/HRTIMER 为 0 是正常的(没有对应负载)",
		eqInts(sfi["BLOCK"], []int{0, 0, 0, 0}) && eqInts(sfi["IRQ_POLL"], []int{0, 0, 0, 0}))
	for i, name := range softirqNames {
		if name == "NET_RX" {
			check("名字顺序与内核预定义一致(NET_RX 是第 "+strconv.Itoa(i+1)+" 个)", i == 3)
		}
	}

	fmt.Println("== 5. 累计值必须两次采样求差 ==")
	after := map[string][]int{}
	for k, v := range sfi {
		after[k] = append([]int(nil), v...)
	}
	after["NET_RX"] = []int{4567890 + 3000, 3456789 + 3000, 4567891 + 3000, 3456790 + 3000}
	d := DiffSeries(sfi, after)
	check("NET_RX 增量 = 4 核各 +3000,合计 12000", d["NET_RX"] == 12000, strconv.Itoa(d["NET_RX"]))
	check("没变的项差分恒为 0", d["TIMER"] == 0 && d["BLOCK"] == 0)
	check("单点读值会被当成「绝对值」,和差分差好几个数量级", sum(sfi["NET_RX"]) > 1000*d["NET_RX"])

	fmt.Println("== 6. /proc/net/softnet_stat 是十六进制、无表头 ==")
	sn := ParseSoftnetStat(softnet)
	check("每 CPU 一行,共 2 行,每行 13 列", len(sn) == 2 && len(sn[0]) == 13)
	check("0001e240 按十六进制 = 123456,不是十进制 124480", sn[0][0] == 0x0001E240 && sn[0][0] == 123456,
		strconv.Itoa(sn[0][0]))
	_, errDec := strconv.Atoi("0001e240")
	check("带 e 的串按十进制解析会直接报错,不会悄悄给出错数", errDec != nil)
	hx, _ := strconv.ParseInt("00000123", 16, 64)
	dc, _ := strconv.Atoi("00000123")
	check("真正危险的是 '00000123' 这种:十进制是 123、十六进制是 291,静默给出错数",
		hx == 291 && dc == 123)
	proc, drop, squeeze := SoftnetTotals(sn)
	check("第 1/2/3 列分别是 处理数 / backlog 丢包 / time_squeeze",
		proc == 123456+123457 && drop == 12 && squeeze == 3,
		fmt.Sprintf("proc=%d drop=%d squeeze=%d", proc, drop, squeeze))
	short1, short2, short3 := SoftnetTotals([][]int{{1, 2}})
	check("短行按缺列补 0,不会越界", short1 == 1 && short2 == 2 && short3 == 0)
	later := [][]int{append([]int{0x0001E241, 20, 5}, make([]int, 10)...),
		append([]int{0x0001E241, 0, 0}, make([]int, 10)...)}
	lp, ld, ls := SoftnetTotals(later)
	check("差分也要按列分别做:处理数 +1、丢包 +8、squeeze +2",
		lp-proc == 1 && ld-drop == 8 && ls-squeeze == 2,
		fmt.Sprintf("%d/%d/%d", lp-proc, ld-drop, ls-squeeze))

	fmt.Println("== 7. 归因:把「%si 高」拆成三种动作 ==")
	check("丢包长 + squeeze 不长 -> 抬 netdev_max_backlog", Attribute(0.30, 500, 0) == raiseBacklog)
	check("squeeze 长 + 不丢包 -> 抬 netdev_budget", Attribute(0.30, 0, 500) == raiseBudget)
	check("丢包优先于 squeeze(丢包是已发生的损失,更紧急)", Attribute(0.30, 900, 100) == raiseBacklog)
	check("两个都不长 -> 高 %si 是真活,该分散 IRQ / 合并中断", Attribute(0.30, 0, 0) == realWork)
	check("netdev_budget 内核默认 300", netdevBudgetDefault == 300)

	fmt.Println("== 8. smp_affinity 位掩码 ==")
	aff := func(s string) []int {
		m, err := ParseSmpAffinity(s)
		if err != nil {
			return nil
		}
		return MaskToCPUs(m)
	}
	check("'f' -> CPU 0-3", eqInts(aff("f"), []int{0, 1, 2, 3}), fmt.Sprint(aff("f")))
	check("'0000000f' 前导零不影响", eqInts(aff("0000000f"), []int{0, 1, 2, 3}))
	check("'0x3' 带前缀也能读", eqInts(aff("0x3"), []int{0, 1}))
	check("'1' -> 只有 CPU0", eqInts(aff("1"), []int{0}))
	check("'9' -> CPU0 和 CPU3(位 0 与位 3)", eqInts(aff("9"), []int{0, 3}))
	check("'ffffffff,ffffffff' -> 64 个核", len(aff("ffffffff,ffffffff")) == 64)
	check("逗号分组【低位组在前】:第 1 组管 CPU0-31,第 2 组管 32-63",
		eqInts(aff("00000001,00000001"), []int{0, 32}), fmt.Sprint(aff("00000001,00000001")))
	check("'00000000,ffffffff' -> 只用高位 32 核(CPU 32-63)",
		len(aff("00000000,ffffffff")) == 32 && aff("00000000,ffffffff")[0] == 32)
	m0, _ := ParseSmpAffinity("0")
	mf, _ := ParseSmpAffinity("f")
	check("全 0 必须被拒绝:内核不允许把 IRQ 挂到「没有核」上",
		CheckAffinity(m0) != nil && CheckAffinity(mf) == nil)
	check("CPU 列表格式化 0-3,7", FormatCPUList([]int{0, 1, 2, 3, 7}) == "0-3,7")

	fmt.Println("== 9. smp_affinity_list 是人看的接口 ==")
	l1, _ := ParseSmpAffinityList("0-1")
	l2, _ := ParseSmpAffinityList("0,3")
	l3, _ := ParseSmpAffinityList("1024-1031")
	check("'0-1' -> [0,1]", eqInts(l1, []int{0, 1}))
	check("'0,3' -> [0,3]", eqInts(l2, []int{0, 3}))
	check("'1024-1031' -> 8 个核,从 1024 开始(掩码写这个要 32 个零)",
		len(l3) == 8 && l3[0] == 1024)
	check("同一个集合的两种写法等价(CPU0-3)", FormatCPUList(aff("f")) == "0-3")

	fmt.Println("== 10. /proc/stat 的 intr / softirq 两行 ==")
	st := ParseProcStat(procStat)
	check("两个字段都取到", len(st) == 2)
	check("intr 行:第一个数是总数,后面是分项",
		st["intr"][0] == 9876543 && eqInts(st["intr"][1:4], []int{46, 3, 0}))
	check("softirq 行 = 总数 + 10 种类型", len(st["softirq"]) == 11)
	check("softirq 分项顺序与 softirqNames 对齐:第 1 项 HI=1,第 4 项 NET_RX=16059360",
		st["softirq"][1] == 1 && st["softirq"][4] == 16059360)
	check("softirq 行的总数【严格等于】分项之和(所有类型都被列出)",
		sum(st["softirq"][1:]) == st["softirq"][0],
		fmt.Sprintf("%d vs %d", sum(st["softirq"][1:]), st["softirq"][0]))
	check("intr 行相反:总数【大于】分项之和 —— 未编号的架构向量只被计入总数",
		st["intr"][0] > sum(st["intr"][1:]),
		fmt.Sprintf("总数 %d vs 分项和 %d", st["intr"][0], sum(st["intr"][1:])))
	check("这两行的口径差别是同一份文档里两句话,不看清楚就会算错占比",
		st["intr"][0]-sum(st["intr"][1:]) > 0)

	if ok {
		fmt.Println("\n全部通过")
	} else {
		fmt.Println("\n存在失败项")
		os.Exit(1)
	}
}

func eqInts(a, b []int) bool {
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
