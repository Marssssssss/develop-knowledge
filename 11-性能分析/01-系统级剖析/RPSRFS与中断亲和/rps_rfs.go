// RPS / RFS / XPS / flow limit —— Go 侧实现（无工具链，人工审查 + 机械核查）。
//
// 口径同 Python 版，全部来自 Linux 内核文档
// "Scaling in the Linux Networking Stack"：
//   https://docs.kernel.org/networking/scaling.html
package main

import (
	"fmt"
	"strconv"
	"strings"
)

// ParseCPUMask 解析与 smp_affinity / rps_cpus 相同的位图格式：
// 逗号分隔的 32 位十六进制字，最低字在前。
func ParseCPUMask(text string) ([]int, error) {
	text = strings.TrimSpace(text)
	var cpus []int
	if text == "" || text == "0" {
		return cpus, nil
	}
	for i, w := range strings.Split(text, ",") {
		v, err := strconv.ParseUint(strings.TrimSpace(w), 16, 64)
		if err != nil {
			return nil, fmt.Errorf("第 %d 个字不是十六进制: %q", i, w)
		}
		for bit := 0; bit < 32; bit++ {
			if v>>uint(bit)&1 == 1 {
				cpus = append(cpus, i*32+bit)
			}
		}
	}
	return cpus, nil
}

// RoundupPow2：rps_sock_flow_entries / rps_flow_cnt 都会向上取整到 2 的幂。
func RoundupPow2(n int) int {
	if n <= 0 {
		return 1
	}
	p := 1
	for p < n {
		p <<= 1
	}
	return p
}

// RPSSelectCPU：flow hash 对 CPU 列表长度取模；列表为空则留在中断 CPU。
func RPSSelectCPU(flowHash uint32, rpsCPUs []int, irqCPU int) int {
	if len(rpsCPUs) == 0 {
		return irqCPU // rps_cpus = 0（默认）⇒ RPS 禁用
	}
	return rpsCPUs[int(flowHash)%len(rpsCPUs)]
}

// RFSDecide：RFS 是否把 current 更新为 desired（防乱序的三条判据）。
func RFSDecide(desired, current, queueHead, recordedTail, nrCPUIDs int, offline []int) int {
	if desired == current {
		return current
	}
	drained := queueHead >= recordedTail
	unset := current >= nrCPUIDs
	isOffline := false
	for _, c := range offline {
		if c == current {
			isOffline = true
			break
		}
	}
	if drained || unset || isOffline {
		return desired
	}
	return current
}

// FlowLimit：入队长度超过 netdev_max_backlog 一半时，按最近 256 个报文的
// per-flow 占比丢大流的新包（默认 ratio = 一半）。
type FlowLimit struct {
	MaxBacklog int
	Ratio      float64
	TableLen   int
	history    []int
}

const flowLimitHistory = 256

func (f *FlowLimit) Threshold() float64 { return float64(f.MaxBacklog) * 0.5 }

func (f *FlowLimit) Active(backlog int) bool {
	return float64(backlog) > f.Threshold()
}

func (f *FlowLimit) ShouldDrop(flowID, backlog int) bool {
	if !f.Active(backlog) || len(f.history) < flowLimitHistory {
		f.push(flowID)
		return false
	}
	cnt := 0
	for _, h := range f.history {
		if h == flowID {
			cnt++
		}
	}
	if float64(cnt)/float64(len(f.history)) > f.Ratio {
		return true // 丢弃的不入历史
	}
	f.push(flowID)
	return false
}

func (f *FlowLimit) push(flowID int) {
	f.history = append(f.history, flowID)
	if len(f.history) > flowLimitHistory {
		f.history = f.history[len(f.history)-flowLimitHistory:]
	}
}

// XPSSelectQueue：CPU → 候选队列，命中多个时用 flow hash 再选一个。
func XPSSelectQueue(m map[int][]int, cpu int, flowHash int, hasHash bool) (int, bool) {
	cands, ok := m[cpu]
	if !ok || len(cands) == 0 {
		return 0, false
	}
	if len(cands) == 1 || !hasHash {
		return cands[0], true
	}
	return cands[flowHash%len(cands)], true
}

func main() {
	mask, err := ParseCPUMask("00000000,00000001")
	if err != nil {
		fmt.Println("parse error:", err)
		return
	}
	fmt.Printf("位图 00000000,00000001 -> %v\n", mask)
	fmt.Printf("roundup(60000) = %d\n", RoundupPow2(60000))
	fmt.Printf("rps_flow_cnt(131072,16) = %d\n", 131072/16)
	fmt.Printf("RPS hash 10 over [0 1 2 3] -> cpu %d\n", RPSSelectCPU(10, []int{0, 1, 2, 3}, 0))
	fmt.Printf("RFS 残留未排空 -> cpu %d（保持 1）\n", RFSDecide(5, 1, 99, 100, 8, nil))
	fmt.Printf("RFS current 下线 -> cpu %d\n", RFSDecide(5, 2, 0, 100, 8, []int{2}))

	f := &FlowLimit{MaxBacklog: 1000, Ratio: 0.5, TableLen: 4096}
	for i := 0; i < 129; i++ {
		f.push(7)
	}
	for i := 0; i < 127; i++ {
		f.push(9)
	}
	fmt.Printf("flow 7 占比过半 ⇒ drop=%v\n", f.ShouldDrop(7, 800))

	cm := map[int][]int{0: {0}, 2: {2, 3}}
	q, ok := XPSSelectQueue(cm, 2, 1, true)
	fmt.Printf("XPS cpu2 hash1 -> queue %d (ok=%v)\n", q, ok)
}
