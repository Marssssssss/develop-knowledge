// 基于类型的序列比对合并（§3.4），从 discoverer.go 拆出。
package main

func formatEqual(f1, f2 []spec) bool {
	if len(f1) != len(f2) {
		return false
	}
	for i := range f1 {
		if !tokMatch(f1[i], f2[i]) {
			return false
		}
	}
	return true
}

// findFD §3.3.3 三条判据。
func findFD(msgs [][]byte) int {
	fmt_ := inferFormat(msgs)
	for k := range fmt_ {
		groups := map[string][][]byte{}
		for _, m := range msgs {
			v := string(tokenize(m)[k].val)
			groups[v] = append(groups[v], m)
		}
		if len(groups) >= fdMaxDistinct { // 判据 1
			continue
		}
		if len(groups) < 2 { // 常量 token 切不出子簇，等于没切
			continue
		}
		biggest := 0
		var subFmts [][]spec
		for _, g := range groups {
			if len(g) > biggest {
				biggest = len(g)
			}
			subFmts = append(subFmts, inferFormat(g))
		}
		if biggest < minCluster { // 判据 2
			continue
		}
		same := false
		for _, s := range subFmts[1:] { // 判据 3
			if formatEqual(subFmts[0], s) {
				same = true
			}
		}
		if same {
			continue
		}
		return k
	}
	return -1
}


type pair struct{ i, j int } // -1 表示 gap

// align Needleman-Wunsch，且只允许同 class 的 token 互相对齐。
func align(f1, f2 []spec) []pair {
	n, m := len(f1), len(f2)
	F := make([][]int, n+1)
	P := make([][]byte, n+1)
	for i := 0; i <= n; i++ {
		F[i] = make([]int, m+1)
		P[i] = make([]byte, m+1)
	}
	for i := 1; i <= n; i++ {
		F[i][0], P[i][0] = i*nwGap, 'U'
	}
	for j := 1; j <= m; j++ {
		F[0][j], P[0][j] = j*nwGap, 'L'
	}
	for i := 1; i <= n; i++ {
		for j := 1; j <= m; j++ {
			sc := neg
			if f1[i-1].cls == f2[j-1].cls {
				sc = nwMismatch
				if tokMatch(f1[i-1], f2[j-1]) {
					sc = nwMatch
				}
			}
			best, dir := F[i-1][j-1]+sc, 'D'
			if F[i-1][j]+nwGap > best {
				best, dir = F[i-1][j]+nwGap, 'U'
			}
			if F[i][j-1]+nwGap > best {
				best, dir = F[i][j-1]+nwGap, 'L'
			}
			F[i][j], P[i][j] = best, dir
		}
	}
	out := []pair{}
	for i, j := n, m; i > 0 || j > 0; {
		switch P[i][j] {
		case 'D':
			out = append(out, pair{i - 1, j - 1})
			i, j = i-1, j-1
		case 'U':
			out = append(out, pair{i - 1, -1})
			i--
		default:
			out = append(out, pair{-1, j - 1})
			j--
		}
	}
	for a, b := 0, len(out)-1; a < b; a, b = a+1, b-1 {
		out[a], out[b] = out[b], out[a]
	}
	return out
}

// canMerge §3.4：gap 约束 + 至多 1 处失配。
func canMerge(f1, f2 []spec) (bool, int) {
	pairs := align(f1, f2)
	textGaps := 0
	for _, p := range pairs {
		if p.i >= 0 && p.j < 0 && f1[p.i].cls == clsT {
			textGaps++
		}
		if p.j >= 0 && p.i < 0 && f2[p.j].cls == clsT {
			textGaps++
		}
	}
	if textGaps > 2 {
		return false, -1
	}
	for side := 1; side <= 2; side++ {
		own, other := f1, f2
		if side == 2 {
			own, other = f2, f1
		}
		run, field := 0, 0
		flush := func() {
			if run == 0 {
				return
			}
			okRun := false
			for _, t := range other {
				if t.cls == clsT && t.size >= run {
					okRun = true
				}
			}
			if !okRun {
				field = -1
			}
			run = 0
		}
		for _, p := range pairs {
			idx := -1
			if side == 1 && p.i >= 0 && p.j < 0 {
				idx = p.i
			}
			if side == 2 && p.j >= 0 && p.i < 0 {
				idx = p.j
			}
			if idx >= 0 && own[idx].cls == clsB {
				run++
			} else {
				flush()
			}
		}
		flush()
		if field == -1 {
			return false, -1
		}
	}
	mm := 0
	for _, p := range pairs {
		if p.i >= 0 && p.j >= 0 && !tokMatch(f1[p.i], f2[p.j]) {
			mm++
		}
	}
	return mm <= 1, mm
}
