// 经典 Levenshtein 与 Damerau-Levenshtein（OSA）距离。
//
// 转写自 Lucene 文档与 LevenshteinAutomata 的语义约定（withTranspositions）：
//   * false → 经典 Levenshtein：插入 / 删除 / 替换，各算 1
//   * true  → Damerau-Levenshtein 的 **OSA** 变体：额外允许相邻两字符交换算 1，
//             但一个字符只能参与一次交换（这是 OSA 与真正的 DL 的区别，Lucene 用前者）
package main

// Levenshtein 经典编辑距离（插入/删除/替换）。
func Levenshtein(a, b []rune) int {
	n, m := len(a), len(b)
	if n == 0 {
		return m
	}
	if m == 0 {
		return n
	}
	prev := make([]int, m+1)
	for j := range prev {
		prev[j] = j
	}
	for i := 1; i <= n; i++ {
		cur := make([]int, m+1)
		cur[0] = i
		for j := 1; j <= m; j++ {
			cost := 1
			if a[i-1] == b[j-1] {
				cost = 0
			}
			// 朴素三选一：删除 / 插入 / 替换
			x := prev[j] + 1
			if cur[j-1]+1 < x {
				x = cur[j-1] + 1
			}
			if prev[j-1]+cost < x {
				x = prev[j-1] + cost
			}
			cur[j] = x
		}
		prev = cur
	}
	return prev[m]
}

// DamerauOSA OSA 变体：允许相邻交换算 1，但一个字符只能参与一次交换。
func DamerauOSA(a, b []rune) int {
	n, m := len(a), len(b)
	if n == 0 {
		return m
	}
	if m == 0 {
		return n
	}
	d := make([][]int, n+1)
	for i := range d {
		d[i] = make([]int, m+1)
		d[i][0] = i
	}
	for j := 0; j <= m; j++ {
		d[0][j] = j
	}
	for i := 1; i <= n; i++ {
		for j := 1; j <= m; j++ {
			cost := 1
			if a[i-1] == b[j-1] {
				cost = 0
			}
			v := d[i-1][j] + 1
			if d[i][j-1]+1 < v {
				v = d[i][j-1] + 1
			}
			if d[i-1][j-1]+cost < v {
				v = d[i-1][j-1] + cost
			}
			if i > 1 && j > 1 && a[i-1] == b[j-2] && a[i-2] == b[j-1] {
				if d[i-2][j-2]+1 < v {
					v = d[i-2][j-2] + 1
				}
			}
			d[i][j] = v
		}
	}
	return d[n][m]
}

// Distance 按 Lucene 的 transpositions 开关选一个。
func Distance(a, b []rune, transpositions bool) int {
	if transpositions {
		return DamerauOSA(a, b)
	}
	return Levenshtein(a, b)
}
