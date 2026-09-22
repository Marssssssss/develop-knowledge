func IsComplete(g *Graph) bool {
	n := len(g.Nodes)
	if n < 2 {
		return true
	}
	e := 0
	for _, u := range g.Nodes {
		e += g.Degree(u)
	}
	return e/2 == n*(n-1)/2
}

// HasPath BFS 判连通。
func HasPath(g *Graph, s, t string) bool {
	seen := map[string]bool{s: true}
	stack := []string{s}
	for len(stack) > 0 {
		v := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if v == t {
			return true
		}
		for w := range g.Adj[v] {
			if !seen[w] {
				seen[w] = true
				stack = append(stack, w)
			}
		}
	}
	return false
}

// MaxCardinalitySearch 每步选与已编号集合邻接最多的结点。
func MaxCardinalitySearch(g *Graph, start string) []string {
	unnumbered := map[string]bool{}
	for _, n := range g.Nodes {
		unnumbered[n] = true
	}
	s := start
	if s == "" {
		s = g.Nodes[0]
	}
	delete(unnumbered, s)
	order := []string{s}
	for len(unnumbered) > 0 {
		best := ""
		score := -1
		for n := range unnumbered {
			c := 0
			for m := range order {
				if g.HasEdge(n, m) {
					c++
				}
			}
			if c > score {
				score = c
				best = n
			}
		}
		delete(unnumbered, best)
		order = append(order, best)
	}
	return order
}

// IsChordal MCS 序下每个「已编号邻居集」都必须是团。
func IsChordal(g *Graph) bool {
	numbered := []string{}
	for _, v := range MaxCardinalitySearch(g, "") {
		cand := []string{}
		for m := range g.Adj[v] {
			if contains(numbered, m) {
				cand = append(cand, m)
			}
		}
		if !IsComplete(g.SubGraph(cand)) {
			return false
		}
		numbered = append(numbered, v)
	}
	return true
}

// CompleteToChordalGraph MCS-M 极小三角化，返回三角化后的图与编号 alpha。
func CompleteToChordalGraph(g *Graph) (*Graph, map[string]int) {
	h := g.Clone()
	alpha := map[string]int{}
	for _, n := range h.Nodes {
		alpha[n] = 0
	}
	if IsChordal(h) {
		return h, alpha
	}
	chords := [][2]string{}
	weight := map[string]int{}
	for _, n := range h.Nodes {
		weight[n] = 0
	}
	unnumbered := append([]string{}, h.Nodes...)
	for i := len(h.Nodes); i > 0; i-- {
		z := unnumbered[0]
		for _, n := range unnumbered[1:] {
			if weight[n] > weight[z] {
				z = n
			}
		}
		unnumbered = removeString(unnumbered, z)
		alpha[z] = i
		update := []string{}
		for _, y := range unnumbered {
			if g.HasEdge(y, z) {
				update = append(update, y)
				continue
			}
			lower := []string{z, y}
			for _, n := range unnumbered {
				if weight[n] < weight[y] {
					lower = append(lower, n)
				}
			}
			if HasPath(h.SubGraph(lower), y, z) {
				update = append(update, y)
				chords = append(chords, [2]string{z, y})
			}
		}
		for _, n := range update {
			weight[n]++
		}
	}
	for _, e := range chords {
		h.AddEdge(e[0], e[1])
	}
	return h, alpha
}

// ChordalGraphCliques MCS 序下的极大团。
func ChordalGraphCliques(g *Graph) [][]string {
	numbered := []string{}
	all := [][]string{}
	for _, v := range MaxCardinalitySearch(g, "") {
		cand := []string{v}
		for m := range g.Adj[v] {
			if contains(numbered, m) {
				cand = append(cand, m)
			}
		}
		sort.Strings(cand)
		all = append(all, cand)
		numbered = append(numbered, v)
	}
	out := [][]string{}
	setOf := func(c []string) map[string]bool {
		m := map[string]bool{}
		for _, x := range c {
			m[x] = true
		}
		return m
	}
	for _, c := range all {
		cs := setOf(c)
		keep := true
		for _, o := range all {
			if len(o) <= len(c) {
				continue
			}
			sub := true
			for x := range cs {
				if !setOf(o)[x] {
					sub = false
				}
			}
			if sub {
				keep = false
			}
		}
		if keep {
			out = append(out, c)
		}
	}
	sort.Slice(out, func(i, j int) bool { return len(out[i]) > len(out[j]) })
	return out
}

// Treewidth 最大团大小 − 1（要求输入已是弦图）。
func Treewidth(g *Graph) int {
	if !IsChordal(g) {
		panic("Input graph is not chordal.")
	}
	best := 0
	for _, c := range ChordalGraphCliques(g) {
		if len(c) > best {
			best = len(c)
		}
	}
	return best - 1
}
