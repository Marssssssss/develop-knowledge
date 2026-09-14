// 协议状态机恢复：Veritas(ACNS 2011) P-PSM 骨架实现。
// 流水线: 消息类型标注(首token+方向) → 逐流转移计数(不跨流) → 概率转移+阈值剪枝 → PFSM。
// 用法: go run pfsm.go   (自检失败即 panic)
package main

import (
	"fmt"
	"math/rand"
	"sort"
	"strings"
)

// ---------- 1. 消息类型标注 ----------
type msg struct {
	dir  byte // 'C' / 'S'
	text string
}

type state struct {
	dir  byte
	typ  string
}

func (s state) String() string { return string(s.dir) + ":" + s.typ }

func label(m msg) state {
	fields := strings.Fields(m.text)
	t := ""
	if len(fields) > 0 {
		t = strings.ToUpper(fields[0])
	}
	return state{m.dir, t}
}

func labelFlow(flow []msg) []state {
	out := make([]state, len(flow))
	for i, m := range flow {
		out[i] = label(m)
	}
	return out
}

// ---------- 2. 转移计数 (逐流, 禁止跨流) ----------
type edgeKey struct{ s, d state }

func transitions(flows [][]msg) map[edgeKey]int {
	C := map[edgeKey]int{}
	for _, flow := range flows {
		seq := labelFlow(flow)
		for i := 0; i+1 < len(seq); i++ {
			C[edgeKey{seq[i], seq[i+1]}]++
		}
	}
	return C
}

// ---------- 3. 概率化 + 剪枝 ----------
type edge struct {
	s, d  state
	count int
	prob  float64
}

func pfsm(C map[edgeKey]int, pMin float64, cMin int) []edge {
	rowTot := map[state]int{}
	for k, c := range C {
		rowTot[k.s] += c
	}
	var edges []edge
	for k, c := range C {
		p := float64(c) / float64(rowTot[k.s])
		if c >= cMin && p >= pMin {
			edges = append(edges, edge{k.s, k.d, c, p})
		}
	}
	sort.Slice(edges, func(i, j int) bool {
		if edges[i].prob != edges[j].prob {
			return edges[i].prob > edges[j].prob
		}
		if edges[i].s.String() != edges[j].s.String() {
			return edges[i].s.String() < edges[j].s.String()
		}
		return edges[i].d.String() < edges[j].d.String()
	})
	return edges
}

func accepts(edges []edge, seq []state) bool {
	ok := map[edgeKey]bool{}
	for _, e := range edges {
		ok[edgeKey{e.s, e.d}] = true
	}
	for i := 0; i+1 < len(seq); i++ {
		if !ok[edgeKey{seq[i], seq[i+1]}] {
			return false
		}
	}
	return true
}

// ---------- 自检 ----------
func selfTest() {
	flows := [][]msg{
		{{'C', "A"}, {'S', "B"}, {'C', "A"}},
		{{'C', "A"}, {'S', "B"}, {'C', "C"}},
	}
	C := transitions(flows)
	if C[edgeKey{{'C', "A"}, {'S', "B"}}] != 2 {
		panic("count wrong")
	}
	// 跨流伪边: 流1结尾 ('C','A') 与 流2开头 ('C','A') 不得产生转移
	if _, ok := C[edgeKey{{'C', "A"}, {'C', "C"}}]; ok {
		panic("cross-flow transition leaked")
	}
	edges := pfsm(C, 0.05, 1)
	found := false
	for _, e := range edges {
		if e.s.String() == "C:A" && e.d.String() == "S:B" && e.count == 2 && e.prob == 1.0 {
			found = true
		}
	}
	if !found {
		panic("expected edge missing")
	}
	// 剪枝: 低频低概率边被删
	C2 := transitions([][]msg{
		{{'X', "A"}, {'X', "B"}},
	})
	for i := 0; i < 100; i++ {
		C2[edgeKey{{'X', "A"}, {'X', "A"}}]++
	}
	for _, e := range pfsm(C2, 0.05, 1) {
		if e.s.typ == "A" && e.d.typ == "B" {
			panic("low-prob edge not pruned")
		}
	}
}

// ---------- 演示: 合成 SMTP 风格流量 ----------
func genSMTPFlows(nFlows, seed int) [][]msg {
	rng := rand.New(rand.NewSource(int64(seed)))
	var flows [][]msg
	for i := 0; i < nFlows; i++ {
		var f []msg
		add := func(d byte, s string) { f = append(f, msg{d, s}) }
		add('C', "EHLO client.example"); add('S', "250-STARTTLS")
		add('C', "MAIL FROM:<a@x>"); add('S', "250 OK")
		for j := 0; j < 1+rng.Intn(3); j++ {
			add('C', "RCPT TO:<b@y>"); add('S', "250 OK")
			if rng.Float64() < 0.2 {
				add('C', "RCPT TO:<bad@z>"); add('S', "550 No such user")
			}
		}
		add('C', "DATA"); add('S', "354 Go ahead")
		add('C', "body line 1"); add('C', "."); add('S', "250 Accepted")
		if rng.Float64() < 0.15 {
			add('C', "EHLO again"); add('S', "250-STARTTLS")
		}
		add('C', "QUIT"); add('S', "221 Bye")
		flows = append(flows, f)
	}
	return flows
}

func main() {
	selfTest()
	flows := genSMTPFlows(30, 1)
	fmt.Println("== 样本: 第一条流的消息类型序列 ==")
	for i, s := range labelFlow(flows[0]) {
		if i > 0 {
			fmt.Print(" -> ")
		}
		fmt.Print(s)
	}
	fmt.Println()
	fmt.Println()
	edges := pfsm(transitions(flows), 0.05, 3)
	fmt.Printf("== 剪枝后 PFSM 边表 (p>=0.05 且 count>=3, 共 %d 条边) ==\n", len(edges))
	for _, e := range edges {
		fmt.Printf("  %-16s -> %-16s  count=%-3d p=%.2f\n", e.s, e.d, e.count, e.prob)
	}
	fmt.Println()
	nOK := 0
	for _, f := range flows {
		if accepts(edges, labelFlow(f)) {
			nOK++
		}
	}
	fmt.Printf("== 流覆盖率回测: %d/%d = %.0f%% ==\n", nOK, len(flows), float64(nOK)/float64(len(flows))*100)
}
