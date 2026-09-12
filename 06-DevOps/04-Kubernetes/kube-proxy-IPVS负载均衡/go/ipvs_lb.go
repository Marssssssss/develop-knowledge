// ipvs_lb.go — kube-proxy IPVS 模式负载均衡调度算法模拟
//
// 权威来源:
//   - kubernetes.io/docs/reference/networking/virtual-ips (IPVS 模式 + 11 个调度算法)
//   - kubernetes.io/blog/2018/07/09/ipvs-based-in-cluster-load-balancing-deep-dive
//
// 实现的调度算法:rr / lc / sh / dh / sed / nq / mh (Maglev 简化版)
package main

import "fmt"

const mhN = 137 // 质数,lookup table size = M * N

type Backend struct {
	IP            string
	Port          int
	Weight        int
	ActiveConn    int
	TotalSelected int
}

type Service struct {
	Name      string
	Backends  []Backend
	RRCursor  int
	Lookup    *MaglevTable
}

type MaglevTable struct {
	size   int
	lookup []int
}

func (t *MaglevTable) Pick(srcIP string) int {
	pos := hashIP(srcIP) % t.size
	for t.lookup[pos] == -1 {
		pos = (pos + 1) % t.size
	}
	return t.lookup[pos]
}

func NewMaglevTable(bs []Backend) *MaglevTable {
	n := len(bs)
	size := n * mhN
	t := &MaglevTable{size: size, lookup: make([]int, size)}
	for i := range t.lookup {
		t.lookup[i] = -1
	}
	for i, b := range bs {
		for k := 0; k < mhN; k++ {
			key := fmt.Sprintf("%s/%d/%d", b.IP, i, k)
			pos := hashIP(key) % size
			for t.lookup[pos] != -1 {
				pos = (pos + 1) % size
			}
			t.lookup[pos] = i
		}
	}
	return t
}

// FNV-1a 32-bit
func hashIP(s string) uint32 {
	h := uint32(2166136261)
	for _, ch := range []byte(s) {
		h ^= uint32(ch)
		h *= 16777619
	}
	return h
}

// rr
func lbRR(svc *Service) int {
	idx := svc.RRCursor % len(svc.Backends)
	svc.RRCursor++
	return idx
}

// lc
func lbLC(svc *Service) int {
	best := 0
	for i := 1; i < len(svc.Backends); i++ {
		if svc.Backends[i].ActiveConn < svc.Backends[best].ActiveConn {
			best = i
		}
	}
	return best
}

// sh
func lbSH(svc *Service, srcIP string) int {
	return int(hashIP(srcIP)) % len(svc.Backends)
}

// dh
func lbDH(svc *Service, dstIP string) int {
	return int(hashIP(dstIP)) % len(svc.Backends)
}

// sed: min (C+1)/U
func lbSED(svc *Service) int {
	best := 0
	bestV := float64(svc.Backends[0].ActiveConn+1) / float64(svc.Backends[0].Weight)
	for i := 1; i < len(svc.Backends); i++ {
		v := float64(svc.Backends[i].ActiveConn+1) / float64(svc.Backends[i].Weight)
		if v < bestV {
			best = i
			bestV = v
		}
	}
	return best
}

// nq
func lbNQ(svc *Service) int {
	for i, b := range svc.Backends {
		if b.ActiveConn == 0 {
			return i
		}
	}
	return lbSED(svc)
}

// ============== Demo 1: rr balance ==============
func demo1RRBalance() {
	fmt.Println("\n========== Demo 1: Round Robin distribution over 100 requests ==========")
	svc := &Service{
		Name:     "demo1-svc",
		Backends: []Backend{{IP: "10.244.0.1"}, {IP: "10.244.0.2"}, {IP: "10.244.0.3"}},
	}
	for i := 0; i < 100; i++ {
		idx := lbRR(svc)
		svc.Backends[idx].TotalSelected++
	}
	for _, b := range svc.Backends {
		fmt.Printf("  backend %s: selected %d times\n", b.IP, b.TotalSelected)
	}
}

// ============== Demo 2: all algorithms ==============
func demo2AlgorithmComparison() {
	fmt.Println("\n========== Demo 2: 11 algorithms comparison ==========")
	svc := &Service{
		Name:     "demo2-svc",
		Backends: []Backend{{IP: "10.244.0.1"}, {IP: "10.244.0.2"}, {IP: "10.244.0.3"}},
	}
	srcIPs := []string{"192.168.1.10", "192.168.1.20", "192.168.1.30"}

	fmt.Println("rr (no src affinity):")
	for i := 0; i < 6; i++ {
		fmt.Printf("  → backend %d\n", lbRR(svc))
	}

	fmt.Println("sh (source hash, 3 src):")
	for _, s := range srcIPs {
		idx := lbSH(svc, s)
		fmt.Printf("  src=%s → backend %s\n", s, svc.Backends[idx].IP)
	}

	fmt.Println("dh (dest hash, same dst → same backend):")
	dst := "10.0.0.1"
	for _, s := range srcIPs {
		idx := lbDH(svc, dst)
		fmt.Printf("  src=%s, dst=%s → backend %s\n", s, dst, svc.Backends[idx].IP)
	}

	for i := 0; i < 100; i++ {
		idx := lbRR(svc)
		svc.Backends[idx].ActiveConn++
	}
	fmt.Printf("after 100 rr requests, active_conn = [%d, %d, %d]\n",
		svc.Backends[0].ActiveConn, svc.Backends[1].ActiveConn, svc.Backends[2].ActiveConn)
	fmt.Printf("lc next pick → backend %s (active_conn=%d)\n",
		svc.Backends[lbLC(svc)].IP, svc.Backends[lbLC(svc)].ActiveConn)
	fmt.Printf("sed next pick → backend %s\n", svc.Backends[lbSED(svc)].IP)
	fmt.Printf("nq next pick → backend %s (prefer active_conn=0, fallback sed)\n",
		svc.Backends[lbNQ(svc)].IP)

	fmt.Println("mh (Maglev, src_hash):")
	mh := NewMaglevTable(svc.Backends)
	for _, s := range srcIPs {
		idx := mh.Pick(s)
		fmt.Printf("  src=%s → backend %s\n", s, svc.Backends[idx].IP)
	}
}

// ============== Demo 3: weighted ==============
func demo3WeightedLB() {
	fmt.Println("\n========== Demo 3: Weighted LB (weight=4,2,1 → ~57%,29%,14%) ==========")
	svc := &Service{
		Name: "demo3-svc",
		Backends: []Backend{
			{IP: "10.244.0.1", Weight: 4},
			{IP: "10.244.0.2", Weight: 2},
			{IP: "10.244.0.3", Weight: 1},
		},
	}
	totalW := 4 + 2 + 1
	cursor := 0
	for i := 0; i < 100; i++ {
		wc := cursor % totalW
		var idx int
		if wc < 4 {
			idx = 0
		} else if wc < 6 {
			idx = 1
		} else {
			idx = 2
		}
		cursor++
		svc.Backends[idx].TotalSelected++
	}
	fmt.Println("wrr over 100 requests:")
	for _, b := range svc.Backends {
		pct := 100.0 * float64(b.TotalSelected) / 100
		fmt.Printf("  backend %s (weight=%d): selected %d times (%.1f%%)\n",
			b.IP, b.Weight, b.TotalSelected, pct)
	}
}

// ============== Demo 4: Session Affinity ==============
func demo4SessionAffinity() {
	fmt.Println("\n========== Demo 4: Session Affinity (persistent per src_ip, 10800s) ==========")
	svc := &Service{
		Name: "demo4-svc",
		Backends: []Backend{
			{IP: "10.244.0.1"}, {IP: "10.244.0.2"}, {IP: "10.244.0.3"},
		},
	}
	mh := NewMaglevTable(svc.Backends)
	src := "192.168.1.10"
	first := mh.Pick(src)
	fmt.Printf("first request from %s → backend %s (hash-decided)\n",
		src, svc.Backends[first].IP)
	for i := 0; i < 5; i++ {
		idx := mh.Pick(src)
		svc.Backends[idx].ActiveConn++
		fmt.Printf("  req %d from %s → backend %s (sticky, active_conn=%d)\n",
			i+1, src, svc.Backends[idx].IP, svc.Backends[idx].ActiveConn)
	}
}

func main() {
	demo1RRBalance()
	demo2AlgorithmComparison()
	demo3WeightedLB()
	demo4SessionAffinity()
}
