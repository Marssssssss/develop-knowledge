// CVSS v4.0 评分：宏向量（MacroVector）+ 类内插值。
//
// 逐行转写官方参考实现 FIRSTdotorg/cvss-v4-calculator 的 cvss_score.js，
// 并对照规范 https://www.first.org/cvss/specification-document 的 8 节。
// v4.0 没有闭式公式：专家组先把全部向量按「定性严重度相当」聚成等价类，
// 查表给每个等价类一个分数，再在类内按「严重度距离 / 深度」插值。
package main

import (
	"fmt"
	"math"
	"sort"
	"strings"
)

// MacroVector 六个等价类的层级拼成 6 位字符串
func MacroVector(sel Sel) string {
	return fmt.Sprintf("%d%d%d%d%d%d", eq1(sel), eq2(sel), eq3(sel), eq4(sel), eq5(sel), eq6(sel))
}

func metricOf(token, name string) string {
	idx := strings.Index(token, name+":")
	if idx < 0 {
		return ""
	}
	rest := token[idx+len(name)+1:]
	if i := strings.Index(rest, "/"); i >= 0 {
		return rest[:i]
	}
	return rest
}

// pickMaxVector 在「各 EQ 最高严重度向量」的笛卡尔积里挑第一个能支配当前向量的
func pickMaxVector(sel Sel, mv string) string {
	d := make([]int, 6)
	for i := 0; i < 6; i++ {
		d[i] = int(mv[i] - '0')
	}
	names := make([]string, 0, len(levels))
	for n := range levels {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, a := range eq1Max[d[0]] {
		for _, b := range eq2Max[d[1]] {
			for _, c := range eq3eq6Max[d[2]][d[5]] {
				for _, e := range eq4Max[d[3]] {
					for _, f := range eq5Max[d[4]] {
						cand := a + b + c + e + f
						good := true
						for _, n := range names {
							want := metricOf(cand, n)
							if want == "" {
								continue
							}
							if levels[n][Resolve(sel, n)]-levels[n][want] < 0 {
								good = false
								break
							}
						}
						if good {
							return cand
						}
					}
				}
			}
		}
	}
	return ""
}

func lowerScore(key string) float64 {
	if v, ok := lookup[key]; ok {
		return v
	}
	return math.NaN()
}

func dist(sel Sel, cand string, names ...string) float64 {
	sum := 0.0
	for _, n := range names {
		want := metricOf(cand, n)
		if want == "" {
			continue
		}
		sum += levels[n][Resolve(sel, n)] - levels[n][want]
	}
	return sum
}

// Score 计算 CVSS v4.0 分数（基础 + 威胁 + 环境合并）
func Score(sel Sel) float64 {
	// 短路：所有影响指标都是 N（官方 Exception for no impact）
	allNone := true
	for _, m := range []string{"VC", "VI", "VA", "SC", "SI", "SA"} {
		if Resolve(sel, m) != "N" {
			allNone = false
			break
		}
	}
	if allNone {
		return 0.0
	}
	mv := MacroVector(sel)
	value := lookup[mv]
	d := make([]int, 6)
	for i := 0; i < 6; i++ {
		d[i] = int(mv[i] - '0')
	}
	e1, e2, e3, e4, e5, e6 := d[0], d[1], d[2], d[3], d[4], d[5]

	next := func(a, b, c, e, f, g int) float64 {
		return lowerScore(fmt.Sprintf("%d%d%d%d%d%d", a, b, c, e, f, g))
	}
	s1 := next(e1+1, e2, e3, e4, e5, e6)
	s2 := next(e1, e2+1, e3, e4, e5, e6)
	s4 := next(e1, e2, e3, e4+1, e5, e6)
	s5 := next(e1, e2, e3, e4, e5+1, e6)
	var s3 float64
	switch {
	case e3 == 0 && e6 == 0:
		// 两个方向都行，官方取分数更高的那个
		left := next(e1, e2, e3, e4, e5, e6+1)
		right := next(e1, e2, e3+1, e4, e5, e6)
		if left > right {
			s3 = left
		} else {
			s3 = right
		}
	case e3 == 1 && e6 == 1, e3 == 0 && e6 == 1:
		s3 = next(e1, e2, e3+1, e4, e5, e6)
	case e3 == 1 && e6 == 0:
		s3 = next(e1, e2, e3, e4, e5, e6+1)
	default:
		// (2,1)：下一级 (3,2) 不存在
		s3 = math.NaN()
	}

	cand := pickMaxVector(sel, mv)
	type term struct {
		available float64
		dist      float64
		depth     float64
		zero      bool
	}
	terms := []term{
		{value - s1, dist(sel, cand, "AV", "PR", "UI"), depth.eq1[e1] * step, false},
		{value - s2, dist(sel, cand, "AC", "AT"), depth.eq2[e2] * step, false},
		{value - s3, dist(sel, cand, "VC", "VI", "VA", "CR", "IR", "AR"),
			depth.eq3eq6[e3][e6] * step, false},
		{value - s4, dist(sel, cand, "SC", "SI", "SA"), depth.eq4[e4] * step, false},
		// EQ5 官方把比例恒定为 0：威胁成熟度只换宏向量，不参与插值
		{value - s5, 0, depth.eq5[e5] * step, true},
	}
	total, count := 0.0, 0
	for _, t := range terms {
		if math.IsNaN(t.available) {
			continue
		}
		count++
		if !t.zero && t.depth != 0 {
			total += t.available * (t.dist / t.depth)
		}
	}
	if count > 0 {
		value -= total / float64(count)
	}
	if value < 0 {
		value = 0
	}
	if value > 10 {
		value = 10
	}
	return math.Floor(value*10+0.5) / 10
}

// Severity 官方定性分级
func Severity(score float64) string {
	switch {
	case score == 0:
		return "None"
	case score < 4.0:
		return "Low"
	case score < 7.0:
		return "Medium"
	case score < 9.0:
		return "High"
	default:
		return "Critical"
	}
}

// ScoreVector 解析 + 打分
func ScoreVector(text string) (float64, error) {
	sel, err := ParseVector(text)
	if err != nil {
		return 0, err
	}
	return Score(sel), nil
}
