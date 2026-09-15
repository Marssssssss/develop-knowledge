// Join 算法最小实现:hash join(build+probe)/ GRACE 分区 / sort-merge。
// 依据 CMU 15-445 (Spring 2023) Lecture 11 归纳。
package main

import (
	"fmt"
	"sort"
)

// hashJoin 基础哈希连接:build 小表 → probe 大表。
func hashJoin(R, S [][2]interface{}) [][2]interface{} {
	table := map[int][]([2]interface{}){}
	for _, r := range R {
		k := r[0].(int)
		table[k] = append(table[k], r)
	}
	var out [][2]interface{}
	for _, s := range S {
		for _, r := range table[s[0].(int)] {
			out = append(out, [2]interface{}{r, s})
		}
	}
	return out
}

// graceHashJoin 分区哈希连接:两表同一 h1 分区,逐对分区再内存 join。
func graceHashJoin(R, S [][2]interface{}, k int) [][2]interface{} {
	pr := make([][][2]interface{}, k)
	ps := make([][][2]interface{}, k)
	for _, r := range R {
		i := r[0].(int) % k
		pr[i] = append(pr[i], r)
	}
	for _, s := range S {
		i := s[0].(int) % k
		ps[i] = append(ps[i], s)
	}
	var out [][2]interface{}
	for i := 0; i < k; i++ {
		out = append(out, hashJoin(pr[i], ps[i])...) // 逐对分区 join
	}
	return out
}

// sortMergeJoin 排序归并连接:双指针 + 重复键全组合。
func sortMergeJoin(R, S [][2]interface{}) [][2]interface{} {
	r2, s2 := append([][2]interface{}{}, R...), append([][2]interface{}{}, S...)
	sort.Slice(r2, func(i, j int) bool { return r2[i][0].(int) < r2[j][0].(int) })
	sort.Slice(s2, func(i, j int) bool { return s2[i][0].(int) < s2[j][0].(int) })
	var out [][2]interface{}
	i, j := 0, 0
	for i < len(r2) && j < len(s2) {
		rk, sk := r2[i][0].(int), s2[j][0].(int)
		switch {
		case rk < sk:
			i++
		case rk > sk:
			j++
		default:
			jj := j
			for jj < len(s2) && s2[jj][0].(int) == rk {
				out = append(out, [2]interface{}{r2[i], s2[jj]})
				jj++
			}
			i++ // 外表前进;同键的下一条外元组重新枚举内表重复段
		}
	}
	return out
}

func countPairs(out [][2]interface{}) int {
	return len(out)
}

func main() {
	R := [][2]interface{}{{1, "a"}, {2, "b"}, {2, "c"}, {3, "d"}, {5, "e"}}
	S := [][2]interface{}{{2, "x"}, {2, "y"}, {3, "z"}, {5, "w"}, {7, "q"}, {1, "r"}}

	hj := hashJoin(R, S)
	gh := graceHashJoin(R, S, 4)
	sm := sortMergeJoin(R, S)
	fmt.Println("hash join pairs:", countPairs(hj))
	fmt.Println("grace  pairs:", countPairs(gh))
	fmt.Println("sort-merge pairs:", countPairs(sm))

	// 静态自检(人工审查替代编译,见 README)
	assert := func(b bool, msg string) {
		if !b {
			panic("assert failed: " + msg)
		}
	}
	assert(countPairs(hj) == 7, "hash 7 pairs (2x2+3+5+1)")
	assert(countPairs(gh) == 7, "grace same result")
	assert(countPairs(sm) == 7, "sort-merge same result")
	fmt.Println("go static checks passed")
}
