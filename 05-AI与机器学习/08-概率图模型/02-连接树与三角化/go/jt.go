// Intersection 两个团的交集（排序后）。
func Intersection(a, b []string) []string {
	out := []string{}
	for _, x := range a {
		if contains(b, x) {
			out = append(out, x)
		}
	}
	sort.Strings(out)
	return out
}

// BuildJunctionTree 最大生成树：边权 = |Ci ∩ Cj|。
func BuildJunctionTree(cliques [][]string) [][3]int {
	n := len(cliques)
	if n == 0 {
		return nil
	}
	inTree := []int{0}
	edges := [][3]int{}
	for len(inTree) < n {
		best := -1
		var bi, bj int
		for _, i := range inTree {
			for j := 0; j < n; j++ {
				if contains(inTree, j) {
					continue
				}
				w := len(Intersection(cliques[i], cliques[j]))
				if w > best {
					best, bi, bj = w, i, j
				}
			}
		}
		if best < 0 {
			break
		}
		edges = append(edges, [3]int{bi, bj, best})
		inTree = append(inTree, bj)
	}
	return edges
}

// CheckRunningIntersection 交性：任意两团的公共变量必须出现在路径上每个团里。
func CheckRunningIntersection(cliques [][]string, edges [][3]int) bool {
	adj := map[int][]int{}
	for _, e := range edges {
		adj[e[0]] = append(adj[e[0]], e[1])
		adj[e[1]] = append(adj[e[1]], e[0])
	}
	for a := 0; a < len(cliques); a++ {
		for b := a + 1; b < len(cliques); b++ {
			sep := Intersection(cliques[a], cliques[b])
			if len(sep) == 0 {
				continue
			}
			prev := map[int]int{a: -1}
			stack := []int{a}
			for len(stack) > 0 {
				v := stack[len(stack)-1]
				stack = stack[:len(stack)-1]
				if v == b {
					break
				}
				for _, w := range adj[v] {
					if _, ok := prev[w]; !ok {
						prev[w] = v
						stack = append(stack, w)
					}
				}
			}
			if _, ok := prev[b]; !ok {
				return false
			}
			for cur := b; cur != -1; cur = prev[cur] {
				for _, x := range sep {
					if !contains(cliques[cur], x) {
						return false
					}
				}
			}
		}
	}
	return true
}

// JunctionTree 结点是团、边是 sepset 的树；校准用 Lauritzen-Spiegelhalter。
type JunctionTree struct {
	Cliques   [][]string
	Potentials []Factor
	Edges     [][3]int
	Beliefs   []Factor
	Sepsets   map[[2]int]Factor
}

// Calibrate 先 collect（叶→根，逆 BFS）再 distribute（根→叶，BFS）。
// divideOut=false 时不除掉旧消息，用来演示「同一条消息被乘两遍」的后果。
func (jt *JunctionTree) Calibrate(divideOut bool) {
	jt.Beliefs = []Factor{}
	for _, f := range jt.Potentials {
		jt.Beliefs = append(jt.Beliefs, f.Copy())
	}
	jt.Sepsets = map[[2]int]Factor{}
	adj := map[int][]int{}
	for _, e := range jt.Edges {
		adj[e[0]] = append(adj[e[0]], e[1])
		adj[e[1]] = append(adj[e[1]], e[0])
	}
	order := [][2]int{}
	seen := map[int]bool{0: true}
	queue := []int{0}
	for len(queue) > 0 {
		v := queue[0]
		queue = queue[1:]
		for _, w := range adj[v] {
			if !seen[w] {
				seen[w] = true
				order = append(order, [2]int{v, w})
				queue = append(queue, w)
			}
		}
	}
	for i := len(order) - 1; i >= 0; i-- {
		jt.update(order[i][1], order[i][0], divideOut)
	}
	for _, e := range order {
		jt.update(e[0], e[1], divideOut)
	}
}

func (jt *JunctionTree) update(sender, receiver int, divideOut bool) {
	sep := Intersection(jt.Cliques[sender], jt.Cliques[receiver])
	drop := []string{}
	for _, v := range jt.Cliques[sender] {
		if !contains(sep, v) {
			drop = append(drop, v)
		}
	}
	sigma := jt.Beliefs[sender].Marginalize(drop)
	key := [2]int{sender, receiver}
	if sender > receiver {
		key = [2]int{receiver, sender}
	}
	if divideOut {
		if mu, ok := jt.Sepsets[key]; ok {
			jt.Beliefs[receiver] = jt.Beliefs[receiver].Product(sigma.Divide(mu))
			jt.Sepsets[key] = sigma
			return
		}
	}
	jt.Beliefs[receiver] = jt.Beliefs[receiver].Product(sigma)
	jt.Sepsets[key] = sigma
}

// Marginal 从任意含该变量的团里读边缘分布。
func (jt *JunctionTree) Marginal(v string) Factor {
	for i, c := range jt.Cliques {
		if contains(c, v) {
			drop := []string{}
			for _, x := range c {
				if x != v {
					drop = append(drop, x)
				}
			}
			return jt.Beliefs[i].Marginalize(drop).Normalize()
		}
	}
	return Factor{}
}
