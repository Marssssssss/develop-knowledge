// Delete operations: borrow/merge rebalancing + recursive delete.
package main

// borrowFromSibling tries to borrow one entry from a sibling so the
// under-full child stays >= half full. Returns true on success.
func borrowFromSibling(parent *Node, idx int) bool {
	L := parent.children[idx]
	if idx+1 < parent.nk+1 {
		R := parent.children[idx+1]
		minK := MIN_KEYS_INNER
		if L.isLeaf {
			minK = MIN_KEYS_LEAF
		}
		if R.nk > minK {
			if L.isLeaf {
				L.keys[L.nk] = R.keys[0]
				L.vals[L.nk] = R.vals[0]
				L.nk++
				copy(R.keys[:], R.keys[1:R.nk])
				copy(R.vals[:], R.vals[1:R.nk])
				R.nk--
				parent.keys[idx] = R.keys[0]
			} else {
				L.keys[L.nk] = parent.keys[idx]
				L.children[L.nk+1] = R.children[0]
				L.nk++
				parent.keys[idx] = R.keys[0]
				copy(R.keys[:], R.keys[1:R.nk])
				for i := 0; i < R.nk-1; i++ {
					R.children[i] = R.children[i+1]
				}
				R.children[R.nk-1] = R.children[R.nk]
				R.nk--
			}
			return true
		}
	}
	if idx > 0 {
		S := parent.children[idx-1]
		minK := MIN_KEYS_INNER
		if L.isLeaf {
			minK = MIN_KEYS_LEAF
		}
		if S.nk > minK {
			if L.isLeaf {
				for i := L.nk; i > 0; i-- {
					L.keys[i] = L.keys[i-1]
					L.vals[i] = L.vals[i-1]
				}
				L.keys[0] = S.keys[S.nk-1]
				L.vals[0] = S.vals[S.nk-1]
				L.nk++
				S.nk--
				parent.keys[idx-1] = L.keys[0]
			} else {
				for i := L.nk; i > 0; i-- {
					L.keys[i] = L.keys[i-1]
					L.children[i+1] = L.children[i]
				}
				L.keys[0] = parent.keys[idx-1]
				L.children[0] = S.children[S.nk]
				L.nk++
				parent.keys[idx-1] = S.keys[S.nk-1]
				S.nk--
			}
			return true
		}
	}
	return false
}

// mergeWithSibling merges L with a sibling and pops parent's separator.
// We prefer merging into the left sibling so range scans stay sorted.
func mergeWithSibling(parent *Node, idx int) {
	L := parent.children[idx]
	if idx > 0 {
		lsib := parent.children[idx-1]
		if L.isLeaf {
			copy(lsib.keys[lsib.nk:], L.keys[:L.nk])
			copy(lsib.vals[lsib.nk:], L.vals[:L.nk])
			lsib.nk += L.nk
			lsib.next = L.next
		} else {
			lsib.keys[lsib.nk] = parent.keys[idx-1]
			copy(lsib.keys[lsib.nk+1:], L.keys[:L.nk])
			for i := 0; i < L.nk; i++ {
				lsib.children[lsib.nk+1+i] = L.children[i]
			}
			lsib.children[lsib.nk+1+L.nk] = L.children[L.nk]
			lsib.nk += 1 + L.nk
		}
		for i := idx - 1; i < parent.nk-1; i++ {
			parent.keys[i] = parent.keys[i+1]
		}
		for i := idx; i < parent.nk; i++ {
			parent.children[i] = parent.children[i+1]
		}
		parent.nk--
	} else {
		rsib := parent.children[idx+1]
		if L.isLeaf {
			copy(L.keys[L.nk:], rsib.keys[:rsib.nk])
			copy(L.vals[L.nk:], rsib.vals[:rsib.nk])
			L.nk += rsib.nk
			L.next = rsib.next
		} else {
			L.keys[L.nk] = parent.keys[idx]
			copy(L.keys[L.nk+1:], rsib.keys[:rsib.nk])
			for i := 0; i < rsib.nk; i++ {
				L.children[L.nk+1+i] = rsib.children[i]
			}
			L.children[L.nk+1+rsib.nk] = rsib.children[rsib.nk]
			L.nk += 1 + rsib.nk
		}
		for i := idx; i < parent.nk-1; i++ {
			parent.keys[i] = parent.keys[i+1]
		}
		for i := idx + 1; i < parent.nk; i++ {
			parent.children[i] = parent.children[i+1]
		}
		parent.nk--
	}
}

func del(n *Node, k int) *Node {
	if n.isLeaf {
		i := keyIdx(n.keys[:n.nk], k)
		if i >= n.nk || n.keys[i] != k {
			return n
		}
		copy(n.keys[i:], n.keys[i+1:n.nk])
		copy(n.vals[i:], n.vals[i+1:n.nk])
		n.nk--
		return n
	}
	i := childIdx(n, k)
	child := n.children[i]
	del(child, k)
	minK := MIN_KEYS_INNER
	if child.isLeaf {
		minK = MIN_KEYS_LEAF
	}
	if child.nk < minK {
		if !borrowFromSibling(n, i) {
			mergeWithSibling(n, i)
		}
	}
	return n
}

// Delete returns the (possibly collapsed) root.
func Delete(root *Node, k int) *Node {
	root = del(root, k)
	if !root.isLeaf && root.nk == 0 {
		root = root.children[0]
	}
	return root
}