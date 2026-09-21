// Package main —— per-CPU 计数器与 jemalloc arena 绑定的 Go 镜像 + 演示入口。
//
// per-CPU 语义依据 Linux core-api/this_cpu_ops：
// this_cpu_* 自带抢占保护，__this_cpu_* 要求调用方已关抢占。
package main

import "fmt"

// SharedCounter 所有 CPU 抢同一条 cacheline。
type SharedCounter struct {
	V             int
	Invalidations int
	Owner         int
	hasOwner      bool
}

// Add 从 cpu 上加 delta。
func (s *SharedCounter) Add(cpu, delta int) {
	if s.hasOwner && s.Owner != cpu {
		s.Invalidations++
	}
	s.Owner = cpu
	s.hasOwner = true
	s.V += delta
}

// PerCpuCounter 每个 CPU 一份，无需同步。
type PerCpuCounter struct {
	V []int
}

// NewPerCpuCounter 构造 ncpu 份计数器。
func NewPerCpuCounter(ncpu int) *PerCpuCounter {
	return &PerCpuCounter{V: make([]int, ncpu)}
}

// ThisCPUAdd 自带抢占保护：RMW 之间不会被迁移。
func (p *PerCpuCounter) ThisCPUAdd(cpu, delta int) { p.V[cpu] += delta }

// UnsafeThisCPUAdd 对应 __this_cpu_add：RMW 之间若被抢占并迁移，更新会落到错误 CPU。
func (p *PerCpuCounter) UnsafeThisCPUAdd(cpu, delta, migrateTo int) {
	tmp := p.V[cpu]
	if migrateTo < 0 {
		p.V[cpu] = tmp + delta
		return
	}
	p.V[migrateTo] = tmp + delta
}

// Total 汇总所有 CPU 的计数。
func (p *PerCpuCounter) Total() int {
	n := 0
	for _, v := range p.V {
		n += v
	}
	return n
}

// JemArenaOf 按线程当前所在 CPU 选 arena（jemalloc opt.percpu_arena）。
func JemArenaOf(cpu, threadsPerCore int, mode string, ncpu, base int) int {
	switch mode {
	case "percpu":
		return cpu
	case "phycpu":
		return cpu / threadsPerCore
	}
	if base < 1 {
		base = 4 * ncpu
	}
	if base < 1 {
		base = 1
	}
	return cpu % base
}

func demoNuma() {
	nodes := []*Node{{Nid: 0, Free: 1000, Latency: 100},
		{Nid: 1, Free: 1000, Latency: 100},
		{Nid: 2, Free: 1000, Latency: 100}}
	m := NewNumaMachine(8, nodes, 2, 60)
	fmt.Println("cpu->node:", m.CPU2Node[0], m.CPU2Node[2], m.CPU2Node[5])
	def, _ := m.Allocate(5, 4, MPOLDEFAULT, nil, nil)
	fmt.Println("DEFAULT on cpu5:", def)
	il, _ := m.Allocate(0, 6, MPOLINTERLEAVE, []int{0, 2}, nil)
	fmt.Println("INTERLEAVE {0,2}:", il)
	wi, _ := m.Allocate(0, 20, MPOLWEIGHTEDINTERLEAVE, []int{0, 1, 2},
		map[int]int{0: 4, 1: 7, 2: 9})
	cnt := map[int]int{}
	for _, n := range wi {
		cnt[n]++
	}
	fmt.Printf("WEIGHTED 4:7:9 -> %v\n", cnt)
}

func demoPerCpu() {
	s := &SharedCounter{}
	for r := 0; r < 3; r++ {
		for c := 0; c < 4; c++ {
			s.Add(c, 1)
		}
	}
	fmt.Printf("shared: v=%d invalidations=%d\n", s.V, s.Invalidations)

	p := NewPerCpuCounter(2)
	p.V[0] = 5
	p.UnsafeThisCPUAdd(0, 1, 1)
	fmt.Printf("__this_cpu_add 迁移后: v=%v total=%d (应为 6)\n", p.V, p.Total())

	t := NewPerCpuCounter(2)
	t.V[0] = 5
	t.ThisCPUAdd(0, 1)
	fmt.Printf("this_cpu_add        : v=%v total=%d\n", t.V, t.Total())

	fmt.Println("arena(percpu, cpu3) =", JemArenaOf(3, 2, "percpu", 8, 0))
	fmt.Println("arena(phycpu, cpu3) =", JemArenaOf(3, 2, "phycpu", 8, 0))
}

func main() {
	demoNuma()
	demoPerCpu()
}
