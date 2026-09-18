// AutoFormat（NDSS 2008）最小复现：执行上下文 → 协议字段树 → 并行/顺序字段。
package main

// hSimilar §3.2.2 脚注 3：实验取 h = 80（共享前缀占整个历史的百分比阈值）。
const hSimilar = 80

// Rec 被读时落盘的 <o, c, s, l> 记录：偏移 / 内容 / 调用栈 / 指令地址。
type Rec struct {
	O int
	C byte
	S []string
	L string
}

// Node 协议字段树的节点：偏移量区间 [Lo, Hi)。
type Node struct {
	Lo       int
	Hi       int
	Children []*Node
	Parallel bool
}

func subsumes(a, b *Node) bool { return a.Lo <= b.Lo && b.Hi <= a.Hi }

func findParent(node, v *Node) *Node {
	for _, c := range node.Children {
		if subsumes(c, v) {
			return findParent(c, v)
		}
	}
	return node
}

func sortKids(n *Node) {
	for i := 1; i < len(n.Children); i++ {
		for j := i; j > 0 && n.Children[j].Lo < n.Children[j-1].Lo; j-- {
			n.Children[j], n.Children[j-1] = n.Children[j-1], n.Children[j]
		}
	}
}

// insert 把一段连续偏移 p 建成节点并挂到「包含它、但其子节点都不包含它」的节点下。
func insert(root *Node, p []int) *Node {
	lo, hi := p[0], p[len(p)-1]+1
	for _, x := range p {
		if x < lo {
			lo = x
		}
		if x+1 > hi {
			hi = x + 1
		}
	}
	v := &Node{Lo: lo, Hi: hi}
	u := findParent(root, v)
	kept := []*Node{}
	for _, c := range u.Children {
		if subsumes(v, c) {
			v.Children = append(v.Children, c)
		} else {
			kept = append(kept, c)
		}
	}
	u.Children = append(kept, v)
	sortKids(u)
	sortKids(v)
	return v
}

// buildFieldTree Algorithm 1：连续偏移 + 相同调用栈 → 合并成一个节点。
func buildFieldTree(log []Rec, msgLen int) *Node {
	root := &Node{Lo: 0, Hi: msgLen}
	if len(log) == 0 {
		return root
	}
	p := []int{log[0].O}
	for i := 1; i < len(log); i++ {
		if log[i].O == log[i-1].O+1 && sameStack(log[i].S, log[i-1].S) {
			p = append(p, log[i].O)
		} else {
			insert(root, p)
			p = []int{log[i].O}
		}
	}
	// 论文 Algorithm 1 的伪代码直接 Return，漏了冲刷最后一段；此处补齐。
	insert(root, p)
	return root
}

func sameStack(a, b []string) bool {
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

// dedup §3.2.1 预处理：连续完全相同的记录只留一条。
func dedup(log []Rec) []Rec {
	out := []Rec{}
	for _, r := range log {
		if len(out) == 0 || !sameRec(r, out[len(out)-1]) {
			out = append(out, r)
		}
	}
	return out
}

func sameRec(a, b Rec) bool {
	return a.O == b.O && a.C == b.C && a.L == b.L && sameStack(a.S, b.S)
}

// historyOf 某偏移的执行历史 = 该偏移被读时记录的调用栈序列。
func historyOf(log []Rec, off int) [][]string {
	hist := [][]string{}
	for _, r := range log {
		if r.O == off {
			hist = append(hist, r.S)
		}
	}
	return hist
}

func stackEq(a, b []string) bool { return sameStack(a, b) }

// similar 脚注 3：共享前缀 ≥ 整个历史的 h%（「整个历史」取较长的一侧，更保守）。
func similar(h1, h2 [][]string, h int) bool {
	if len(h1) == 0 || len(h2) == 0 {
		return false
	}
	n := len(h1)
	if len(h2) < n {
		n = len(h2)
	}
	k := 0
	for k < n && stackEq(h1[k], h2[k]) {
		k++
	}
	lg := len(h1)
	if len(h2) > lg {
		lg = len(h2)
	}
	return k*100 >= h*lg
}

func lowest(n *Node) int {
	if len(n.Children) > 0 {
		return n.Children[0].Lo
	}
	return n.Lo
}

// markParallel Algorithm 2：逐层 BFS，把执行历史相似的子节点合并成并行字段。
func markParallel(root *Node, log []Rec) {
	queue := []*Node{root}
	for len(queue) > 0 {
		v := queue[0]
		queue = queue[1:]
		kids := v.Children
		if len(kids) < 2 {
			queue = append(queue, kids...)
			continue
		}
		hists := make([][][]string, len(kids))
		for i, k := range kids {
			hists[i] = historyOf(log, lowest(k))
		}
		used := make([]bool, len(kids))
		var newKids []*Node
		for i := range kids {
			if used[i] {
				continue
			}
			grp := []int{i}
			used[i] = true
			for j := i + 1; j < len(kids); j++ {
				if !used[j] && similar(hists[i], hists[j], hSimilar) {
					grp = append(grp, j)
					used[j] = true
				}
			}
			if len(grp) >= 2 {
				par := &Node{Parallel: true, Lo: kids[grp[0]].Lo, Hi: kids[grp[0]].Hi}
				for _, g := range grp {
					if kids[g].Lo < par.Lo {
						par.Lo = kids[g].Lo
					}
					if kids[g].Hi > par.Hi {
						par.Hi = kids[g].Hi
					}
					par.Children = append(par.Children, kids[g])
				}
				newKids = append(newKids, par)
			} else {
				newKids = append(newKids, kids[grp[0]])
			}
		}
		v.Children = newKids
		sortKids(v)
		queue = append(queue, kids...)
	}
}

// walk 前序遍历 node 的子树，只列叶子与并行字段节点（遇到并行字段不再下钻）。
func walk(node *Node) []*Node {
	lst := []*Node{}
	st := []*Node{}
	for i := len(node.Children) - 1; i >= 0; i-- {
		st = append(st, node.Children[i])
	}
	for len(st) > 0 {
		n := st[len(st)-1]
		st = st[:len(st)-1]
		if len(n.Children) == 0 || n.Parallel {
			lst = append(lst, n)
		}
		if !n.Parallel {
			for i := len(n.Children) - 1; i >= 0; i-- {
				st = append(st, n.Children[i])
			}
		}
	}
	return lst
}

func collectParallel(n *Node, acc []*Node) []*Node {
	for _, c := range n.Children {
		if c.Parallel {
			acc = append(acc, c)
		}
		acc = collectParallel(c, acc)
	}
	return acc
}

// sequentialFields §3.2.2：先对整棵树前序遍历；再对每个并行字段的子树递归走一遍。
func sequentialFields(root *Node) [][]*Node {
	out := [][]*Node{walk(root)}
	for _, p := range collectParallel(root, nil) {
		out = append(out, walk(p))
	}
	return out
}
