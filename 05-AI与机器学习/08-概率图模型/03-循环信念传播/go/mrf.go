package main

import "sort"

// BruteForceMarginals 暴力枚举真值（仅适用于小规模）。
func BruteForceMarginals(m *MRF) map[string][2]float64 {
	n := len(m.Nodes)
	joint := make([]float64, 1<<n)
	z := 0.0
	for mask := 0; mask < 1<<n; mask++ {
		p := 1.0
		for i, node := range m.Nodes {
			x := (mask >> (n - 1 - i)) & 1
			p *= m.NodePot[node][x]
		}
		for _, e := range m.Edges {
			iu, iv := indexOf(m.Nodes, e[0]), indexOf(m.Nodes, e[1])
			xu := (mask >> (n - 1 - iu)) & 1
			xv := (mask >> (n - 1 - iv)) & 1
			p *= m.Psi(e[0], e[1])[xu][xv]
		}
		joint[mask] = p
		z += p
	}
	out := map[string][2]float64{}
	for i, node := range m.Nodes {
		p1 := 0.0
		for mask := 0; mask < 1<<n; mask++ {
			if (mask>>(n-1-i))&1 == 1 {
				p1 += joint[mask]
			}
		}
		p1 /= z
		out[node] = [2]float64{1 - p1, p1}
	}
	return out
}

func indexOf(xs []string, x string) int {
	for i, v := range xs {
		if v == x {
			return i
		}
	}
	return -1
}

// MakeCycle 生成 n 个结点的环。
func MakeCycle(n int, eta float64) *MRF {
	nodes := make([]string, n)
	for i := range nodes {
		nodes[i] = string(rune('A' + i))
	}
	edges := [][2]string{}
	ep := map[string][2][2]float64{}
	for i := 0; i < n; i++ {
		e := [2]string{nodes[i], nodes[(i+1)%n]}
		edges = append(edges, e)
		ep[edgeKey(e[0], e[1])] = SymmetricPotential(eta)
	}
	np := map[string][2]float64{}
	for _, x := range nodes {
		np[x] = [2]float64{1, 1}
	}
	return NewMRF(nodes, edges, np, ep)
}

// SortedKeys 仅为输出稳定排序用。
func SortedKeys(m *MRF) []string {
	out := append([]string{}, m.Nodes...)
	sort.Strings(out)
	return out
}
