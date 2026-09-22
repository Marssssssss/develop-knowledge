package main

// MaximalCliques Bron–Kerbosch 枚举极大团（结果按字典序排序，便于比较）。
func MaximalCliques(g *Graph) [][]string {
	res := [][]string{}
	var expand func(r, p, x map[string]bool)
	expand = func(r, p, x map[string]bool) {
		if len(p) == 0 && len(x) == 0 {
			c := []string{}
			for n := range r {
				c = append(c, n)
			}
			sort.Strings(c)
			res = append(res, c)
			return
		}
		// pivot：与 p 交集最大的点
		pivot := ""
		best := -1
		for n := range union(p, x) {
			c := 0
			for m := range p {
				if g.HasEdge(n, m) {
					c++
				}
			}
			if c > best {
				best = c
				pivot = n
			}
		}
		for v := range diff(p, g.adj[pivot]) {
			nr := copySet(r)
			nr[v] = true
			expand(nr, intersect(p, g.adj[v]), intersect(x, g.adj[v]))
			delete(p, v)
			x[v] = true
		}
	}
	all := map[string]bool{}
	for _, n := range g.nodes {
		all[n] = true
	}
	expand(map[string]bool{}, all, map[string]bool{})
	return res
}

// IsChordal 最大势搜索：每个被编号结点的「已编号邻居集」都必须是团。
func IsChordal(g *Graph) bool {
	if len(g.nodes) == 0 {
		return true
	}
	numbered := map[string]bool{}
	unnumbered := map[string]bool{}
	for _, n := range g.nodes {
		unnumbered[n] = true
	}
	for len(unnumbered) > 0 {
		best := ""
		score := -1
		for n := range unnumbered {
			c := 0
			for m := range numbered {
				if g.HasEdge(n, m) {
					c++
				}
			}
			if c > score {
				score = c
				best = n
			}
		}
		cand := []string{}
		for m := range g.adj[best] {
			if numbered[m] {
				cand = append(cand, m)
			}
		}
		for i := 0; i < len(cand); i++ {
			for j := i + 1; j < len(cand); j++ {
				if !g.HasEdge(cand[i], cand[j]) {
					return false
				}
			}
		}
		delete(unnumbered, best)
		numbered[best] = true
	}
	return true
}

func union(a, b map[string]bool) map[string]bool {
	out := map[string]bool{}
	for k := range a {
		out[k] = true
	}
	for k := range b {
		out[k] = true
	}
	return out
}

func diff(a, b map[string]bool) map[string]bool {
	out := map[string]bool{}
	for k := range a {
		if !b[k] {
			out[k] = true
		}
	}
	return out
}

func intersect(a, b map[string]bool) map[string]bool {
	out := map[string]bool{}
	for k := range a {
		if b[k] {
			out[k] = true
		}
	}
	return out
}

func copySet(a map[string]bool) map[string]bool {
	out := map[string]bool{}
	for k := range a {
		out[k] = true
	}
	return out
}
