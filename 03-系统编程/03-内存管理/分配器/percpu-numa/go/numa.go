// Package main —— NUMA 内存策略的 Go 镜像（与 python/main.py 同构）。
//
// 语义依据 Linux man-pages mbind(2) / set_mempolicy(2) / numa(7)。
package main

import "sort"

// 内存策略模式（与内核 MPOL_* 对应）
const (
	MPOLDEFAULT = iota
	MPOLBIND
	MPOLINTERLEAVE
	MPOLWEIGHTEDINTERLEAVE
	MPOLPREFERRED
	MPOLPREFERREDMANY
	MPOLLOCAL
)

// mbind flags
const (
	MPOLMFSTRICT     = 1 << 0
	MPOLMFMOVE       = 1 << 1
	MPOLMFMOVEALL    = 1 << 2
	MPOLFSTATICNODES = 1 << 3
	MPOLFRELNODES    = 1 << 4
	MPOLFNUMABAL     = 1 << 5
)

// Node 是一个 NUMA 节点。
type Node struct {
	Nid      int
	Free     int
	Latency  int
}

// Take 扣减空闲页数，成功返回 true。
func (n *Node) Take(k int) bool {
	if n.Free < k {
		return false
	}
	n.Free -= k
	return true
}

// NumaMachine 是一台 NUMA 机器。
type NumaMachine struct {
	NCPU           int
	Nodes          []*Node
	ThreadsPerCore int
	HopLatency     int
	CPU2Node       map[int]int
	Dist           map[[2]int]int
}

// NewNumaMachine 按「CPU 顺序分组到各节点」构造一台机器。
func NewNumaMachine(ncpu int, nodes []*Node, threadsPerCore, hopLatency int) *NumaMachine {
	m := &NumaMachine{
		NCPU: ncpu, Nodes: nodes, ThreadsPerCore: threadsPerCore,
		HopLatency: hopLatency, CPU2Node: map[int]int{}, Dist: map[[2]int]int{},
	}
	per := ncpu / len(nodes)
	if per < 1 {
		per = 1
	}
	for c := 0; c < ncpu; c++ {
		n := c / per
		if n > len(nodes)-1 {
			n = len(nodes) - 1
		}
		m.CPU2Node[c] = n
	}
	for _, a := range nodes {
		for _, b := range nodes {
			d := a.Nid - b.Nid
			if d < 0 {
				d = -d
			}
			m.Dist[[2]int{a.Nid, b.Nid}] = d
		}
	}
	return m
}

// Node 按 id 取节点。
func (m *NumaMachine) Node(nid int) *Node {
	for _, n := range m.Nodes {
		if n.Nid == nid {
			return n
		}
	}
	return nil
}

// Latency 返回 from 节点访问 to 节点的延迟。
func (m *NumaMachine) Latency(from, to int) int {
	return m.Node(to).Latency + m.Dist[[2]int{from, to}]*m.HopLatency
}

// Allocate 按策略分配 npages 页，返回节点号列表与错误字符串。
func (m *NumaMachine) Allocate(cpu, npages, mode int, nodemask []int, weights map[int]int) ([]int, string) {
	local := m.CPU2Node[cpu]
	mask := append([]int{}, nodemask...)
	sort.Ints(mask)
	out := []int{}

	if mode == MPOLDEFAULT || mode == MPOLLOCAL {
		for i := 0; i < npages; i++ {
			out = append(out, local)
			m.Node(local).Take(1)
		}
		return out, ""
	}

	if mode == MPOLBIND {
		if len(mask) == 0 {
			return nil, "EINVAL"
		}
		for i := 0; i < npages; i++ {
			best := -1
			for _, n := range mask {
				if m.Node(n).Free < 1 {
					continue
				}
				if best < 0 || m.Dist[[2]int{local, n}] < m.Dist[[2]int{local, best}] {
					best = n
				}
			}
			if best < 0 {
				return nil, "ENOMEM"
			}
			m.Node(best).Take(1)
			out = append(out, best)
		}
		return out, ""
	}

	if mode == MPOLINTERLEAVE {
		if len(mask) == 0 {
			return nil, "EINVAL"
		}
		for i := 0; i < npages; i++ {
			n := mask[i%len(mask)]
			m.Node(n).Take(1)
			out = append(out, n)
		}
		return out, ""
	}

	if mode == MPOLWEIGHTEDINTERLEAVE {
		seq := []int{}
		for _, n := range mask {
			w := 1
			if v, ok := weights[n]; ok {
				w = v
			}
			for k := 0; k < w; k++ {
				seq = append(seq, n)
			}
		}
		if len(seq) == 0 {
			return nil, "EINVAL"
		}
		for i := 0; i < npages; i++ {
			n := seq[i%len(seq)]
			m.Node(n).Take(1)
			out = append(out, n)
		}
		return out, ""
	}

	if mode == MPOLPREFERRED || mode == MPOLPREFERREDMANY {
		for i := 0; i < npages; i++ {
			if len(mask) == 0 {
				m.Node(local).Take(1)
				out = append(out, local)
				continue
			}
			pick := local
			for _, n := range mask {
				if m.Node(n).Free >= 1 {
					pick = n
					break
				}
			}
			m.Node(pick).Take(1)
			out = append(out, pick)
		}
		return out, ""
	}
	return nil, "EINVAL"
}

// Validate 复现 man page 的 EINVAL / EPERM 清单，返回 errno 或空串。
func Validate(mode int, nodemask []int, flags int, hasCap bool) string {
	if flags&MPOLFSTATICNODES != 0 && flags&MPOLFRELNODES != 0 {
		return "EINVAL"
	}
	if flags&MPOLFNUMABAL != 0 && mode != MPOLBIND {
		return "EINVAL"
	}
	if mode == MPOLDEFAULT && len(nodemask) > 0 {
		return "EINVAL"
	}
	if (mode == MPOLBIND || mode == MPOLINTERLEAVE) && len(nodemask) == 0 {
		return "EINVAL"
	}
	if flags&MPOLMFMOVEALL != 0 && !hasCap {
		return "EPERM"
	}
	return ""
}

// Mbind 返回 (errno 或 "ok", 迁移后的页)。页表示为 [节点号, 是否独占]。
func Mbind(pages [][2]int, nodemask []int, flags int, hasCap bool) (string, [][2]int) {
	if flags&MPOLMFMOVEALL != 0 && !hasCap {
		return "EPERM", pages
	}
	mask := append([]int{}, nodemask...)
	sort.Ints(mask)
	inMask := map[int]bool{}
	for _, n := range mask {
		inMask[n] = true
	}
	if flags&MPOLMFSTRICT != 0 {
		for _, p := range pages {
			if !inMask[p[0]] {
				return "EIO", pages
			}
		}
	}
	out := [][2]int{}
	for _, p := range pages {
		if inMask[p[0]] {
			out = append(out, p)
			continue
		}
		if flags&MPOLMFMOVEALL != 0 {
			out = append(out, [2]int{mask[0], p[1]})
		} else if flags&MPOLMFMOVE != 0 && p[1] == 1 {
			out = append(out, [2]int{mask[0], p[1]})
		} else {
			out = append(out, p)
		}
	}
	return "ok", out
}
