// B+ Tree bulk-load and ASCII visualization.
//
// Moved verbatim out of bptree.go to respect the <=300 line hard limit
// (_docs/OPTIMIZATION.md 1.1). Same package, so nothing is re-exported and no
// call site changes: Go compiles every .go file in the directory as one
// package, which makes this split a pure text move with zero semantic risk.
//
// BulkLoad builds from a SORTED input in O(N) by filling leaves sequentially
// and then folding them bottom-up into inner levels - no path tracking and no
// split-on-the-way-up, which is what makes it linear instead of N log N.
// visualize is the debug printer; it is the only user of fmt in the package.
package main

import "fmt"
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

