// B+ Tree index - minimal Go implementation.
//
// Mirrors bptree.py and bptree.c in structure; uses M = 4 (max 3 keys/node).
// Inner nodes route via separator keys; leaf nodes hold (key, value) pairs
// and are linked left-to-right via sibling pointers for range scans.
//
// Invariants (per CMU 15-445 Lecture 08 / OpenDSA 7.2 "B-Trees"):
//   * Every leaf at the same depth (perfect balance).
//   * Inner nodes (excluding root) hold >= MIN_KEYS_INNER keys.
//   * Leaves hold >= MIN_KEYS_LEAF keys.
//   * Splits propagate upward; root grows when the root itself splits.
//   * Delete rebalances via borrow-from-sibling, else merges with sibling.
//
// Build: go build .
// Run:   ./bptree
package main

import "fmt"

const (
	M              = 4
	MAX_KEYS       = M - 1
	MIN_KEYS_LEAF  = (M+1)/2 - 1 // 1 for M=4
	MIN_KEYS_INNER = M/2 - 1     // 1 for M=4
)

// Node holds both leaf and inner variants in one struct (tagged via isLeaf).
// Go has no native union, so we keep both fields and use isLeaf to select.
type Node struct {
	isLeaf   bool
	nk       int
	keys     [MAX_KEYS + 1]int    // +1 = split overflow slot
	vals     [MAX_KEYS + 1]string // only for leaves
	next     *Node                // sibling chain, only for leaves
	children [M + 1]*Node         // +1 = split overflow slot, only for inner
}

func leafNew() *Node  { return &Node{isLeaf: true} }
func innerNew() *Node { return &Node{isLeaf: false} }

func freeAll(n *Node) {
	if n == nil {
		return
	}
	if !n.isLeaf {
		for i := 0; i <= n.nk; i++ {
			freeAll(n.children[i])
		}
	}
}

// childIdx returns the smallest i such that keys[i] > k; else nk.
func childIdx(n *Node, k int) int {
	lo, hi := 0, n.nk
	for lo < hi {
		mid := (lo + hi) / 2
		if n.keys[mid] <= k {
			lo = mid + 1
		} else {
			hi = mid
		}
	}
	return lo
}

// keyIdx returns the smallest i such that keys[i] >= k; else len.
func keyIdx(keys []int, k int) int {
	lo, hi := 0, len(keys)
	for lo < hi {
		mid := (lo + hi) / 2
		if keys[mid] < k {
			lo = mid + 1
		} else {
			hi = mid
		}
	}
	return lo
}

// Search performs a point lookup. Returns ("", false) if key is absent.
func Search(root *Node, k int) (string, bool) {
	n := root
	for !n.isLeaf {
		n = n.children[childIdx(n, k)]
	}
	i := keyIdx(n.keys[:n.nk], k)
	if i < n.nk && n.keys[i] == k {
		return n.vals[i], true
	}
	return "", false
}

// RangeQuery walks the leaf sibling chain to collect keys in [lo, hi].
func RangeQuery(root *Node, lo, hi int) [][2]any {
	var out [][2]any
	if root == nil {
		return out
	}
	n := root
	for !n.isLeaf {
		n = n.children[childIdx(n, lo)]
	}
	for n != nil {
		for i := 0; i < n.nk; i++ {
			switch {
			case n.keys[i] < lo:
				continue
			case n.keys[i] > hi:
				return out
			}
			out = append(out, [2]any{n.keys[i], n.vals[i]})
		}
		n = n.next
	}
	return out
}

// splitLeaf splits an over-full leaf in half; returns (copy-up key, new right leaf).
func splitLeaf(L *Node) (int, *Node) {
	mid := L.nk / 2
	R := leafNew()
	R.nk = L.nk - mid
	copy(R.keys[:], L.keys[mid:L.nk])
	copy(R.vals[:], L.vals[mid:L.nk])
	L.nk = mid
	R.next = L.next
	L.next = R
	return R.keys[0], R // copy up
}

// splitInner splits an over-full inner node; middle key is *pushed up*.
// Returns (push-up key, new right inner).
func splitInner(I *Node) (int, *Node) {
	mid := I.nk / 2
	up := I.keys[mid]
	R := innerNew()
	R.nk = I.nk - mid - 1
	for i := 0; i < R.nk; i++ {
		R.keys[i] = I.keys[mid+1+i]
		R.children[i] = I.children[mid+1+i]
	}
	R.children[R.nk] = I.children[I.nk]
	I.nk = mid
	return up, R
}

// leafInsert inserts (k, v) into L (assumed not over-full). Returns true if
// newly inserted, false if k existed (and got value-updated).
func leafInsert(L *Node, k int, v string) bool {
	i := keyIdx(L.keys[:L.nk], k)
	if i < L.nk && L.keys[i] == k {
		L.vals[i] = v
		return false
	}
	for j := L.nk; j > i; j-- {
		L.keys[j] = L.keys[j-1]
		L.vals[j] = L.vals[j-1]
	}
	L.keys[i] = k
	L.vals[i] = v
	L.nk++
	return true
}

// Insert returns the (possibly new) root.
func Insert(root *Node, k int, v string) *Node {
	var path [64]*Node
	var idxPath [64]int
	depth := 0

	n := root
	for !n.isLeaf {
		i := childIdx(n, k)
		path[depth] = n
		idxPath[depth] = i
		depth++
		n = n.children[i]
	}
	if !leafInsert(n, k, v) {
		return root
	}

	var pushedKey int
	var pushedNode *Node
	if n.nk > MAX_KEYS {
		pushedKey, pushedNode = splitLeaf(n)
	}
	for pushedNode != nil && depth > 0 {
		depth--
		par := path[depth]
		idx := idxPath[depth]
		for j := par.nk; j > idx; j-- {
			par.keys[j] = par.keys[j-1]
		}
		par.keys[idx] = pushedKey
		for j := par.nk + 1; j > idx+1; j-- {
			par.children[j] = par.children[j-1]
		}
		par.children[idx+1] = pushedNode
		par.nk++
		if par.nk <= MAX_KEYS {
			pushedNode = nil
			break
		}
		pushedKey, pushedNode = splitInner(par)
	}
	if pushedNode != nil {
		nr := innerNew()
		nr.keys[0] = pushedKey
		nr.children[0] = root
		nr.children[1] = pushedNode
		nr.nk = 1
		return nr
	}
	return root
}

// BulkLoad builds a B+ tree from a SORTED (keys, vals) pair. O(N).
func BulkLoad(keys []int, vals []string) *Node {
	if len(keys) == 0 {
		return leafNew()
	}
	var leaves []*Node
	cur := leafNew()
	for i := 0; i < len(keys); i++ {
		if cur.nk == MAX_KEYS {
			leaves = append(leaves, cur)
			cur = leafNew()
		}
		cur.keys[cur.nk] = keys[i]
		cur.vals[cur.nk] = vals[i]
		cur.nk++
	}
	if cur.nk > 0 {
		leaves = append(leaves, cur)
	}
	for i := 0; i+1 < len(leaves); i++ {
		leaves[i].next = leaves[i+1]
	}
	level := leaves
	for len(level) > 1 {
		var next []*Node
		i := 0
		for i < len(level) {
			par := innerNew()
			par.children[0] = level[i]
			j := i + 1
			for j < len(level) && par.nk < MAX_KEYS {
				par.keys[par.nk] = level[j].keys[0]
				par.children[par.nk+1] = level[j]
				par.nk++
				j++
			}
			next = append(next, par)
			i = j
		}
		level = next
	}
	return level[0]
}

func visualize(n *Node, prefix string, isLast bool) {
	branch := prefix
	if isLast {
		branch += "+-- "
	} else {
		branch += "|-- "
	}
	if n.isLeaf {
		fmt.Printf("%s[leaf ", branch)
		for i := 0; i < n.nk; i++ {
			if i > 0 {
				fmt.Print(", ")
			}
			fmt.Printf("%d:%s", n.keys[i], n.vals[i])
		}
		if n.next != nil {
			fmt.Println("] -> next")
		} else {
			fmt.Println("]")
		}
		return
	}
	fmt.Printf("%s[inner keys=", branch)
	for i := 0; i < n.nk; i++ {
		if i > 0 {
			fmt.Print(",")
		}
		fmt.Printf("%d", n.keys[i])
	}
	fmt.Println("]")
	nextPrefix := prefix
	if isLast {
		nextPrefix += "    "
	} else {
		nextPrefix += "|   "
	}
	for i := 0; i <= n.nk; i++ {
		visualize(n.children[i], nextPrefix, i == n.nk)
	}
}

func countLeaves(n *Node) int {
	if n.isLeaf {
		return 1
	}
	c := 0
	for i := 0; i <= n.nk; i++ {
		c += countLeaves(n.children[i])
	}
	return c
}