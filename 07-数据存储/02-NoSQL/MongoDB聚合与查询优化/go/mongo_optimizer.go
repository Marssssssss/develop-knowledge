// mongo_optimizer.go — 序列重排 R1/R2/R4 与合并 R5~R8 + Optimize 入口(同包 main)
//
// 权威来源:MongoDB manual: Aggregation Pipeline Optimization
//   https://www.mongodb.com/docs/manual/core/aggregation-pipeline-optimization/
//   * "MongoDB moves any filters in the $match stage that do not require values
//      computed in the projection stage to a new $match stage before the projection."
//   * "the $match moves before the $sort" / "the $skip moves before $project"
//   * "coalesces the $limit into the $sort"(中间有 $unwind/$group 则不合并)
//   * "coalescence occurs after any sequence reordering optimization"
// 口径差异:R3($redact+$match)与 R9($lookup+$unwind)仅在 Python 版实现。
package main

import "sort"

// ---------------------------------------------------------------- 重排

func r1Pushdown(ss []Stage) ([]Stage, int) {
	applied := 0
	i := 0
	for i < len(ss) {
		if ss[i].Name != "$match" {
			i++
			continue
		}
		runStart := i
		for runStart-1 >= 0 && isProjection(ss[runStart-1]) {
			runStart--
		}
		if runStart == i {
			i++
			continue
		}
		effs := map[int]FieldEffect{}
		for idx := runStart; idx < i; idx++ {
			effs[idx] = fieldEffect(ss[idx])
		}
		groups := map[int][]map[string]any{}
		stay := []map[string]any{}
		for _, f := range splitMatchFilters(ss[i].Spec) {
			fields := filterFields(f.Spec)
			pos := i
			for idx := i - 1; idx >= runStart; idx-- {
				blocked := false
				for name := range fields {
					if effs[idx].Affects(name) {
						blocked = true
						break
					}
				}
				if blocked {
					break
				}
				pos = idx
			}
			if pos < i {
				groups[pos] = append(groups[pos], f.Spec)
			} else {
				stay = append(stay, f.Spec)
			}
		}
		if len(groups) == 0 {
			i++
			continue
		}
		positions := make([]int, 0, len(groups))
		for p := range groups {
			positions = append(positions, p)
		}
		sort.Ints(positions)
		next := []Stage{}
		next = append(next, ss[:runStart]...)
		for idx := runStart; idx < i; idx++ {
			for _, p := range positions {
				if p == idx {
					next = append(next, st("$match", mergeFilters(groups[p])))
				}
			}
			next = append(next, ss[idx])
		}
		if len(stay) > 0 {
			next = append(next, st("$match", mergeFilters(stay)))
		}
		next = append(next, ss[i+1:]...)
		ss = next
		applied++
		i = 0
	}
	return ss, applied
}

func r2SortMatch(ss []Stage) ([]Stage, int) {
	applied := 0
	for i := 0; i+1 < len(ss); i++ {
		if ss[i].Name == "$sort" && ss[i+1].Name == "$match" {
			ss[i], ss[i+1] = ss[i+1], ss[i]
			applied++
		}
	}
	return ss, applied
}

func r4ProjectionSkip(ss []Stage) ([]Stage, int) {
	applied := 0
	for i := 0; i+1 < len(ss); i++ {
		if (ss[i].Name == "$project" || ss[i].Name == "$unset") && ss[i+1].Name == "$skip" {
			ss[i], ss[i+1] = ss[i+1], ss[i]
			applied++
		}
	}
	return ss, applied
}

// ---------------------------------------------------------------- 合并

func r5SortLimit(ss []Stage) ([]Stage, int) {
	applied := 0
	for i := 0; i < len(ss); i++ {
		if ss[i].Name != "$sort" {
			continue
		}
		if _, done := ss[i].Spec["limit"]; done {
			continue
		}
		skips := 0
		found := -1
		for j := i + 1; j < len(ss); j++ {
			switch ss[j].Name {
			case "$limit":
				found = j
			case "$skip":
				if n, ok := ss[j].Spec["skip"].(int); ok {
					skips += n
				}
				continue
			default:
				if countChanging(ss[j].Name) || ss[j].Name == "$out" || ss[j].Name == "$merge" || ss[j].Name == "$facet" || ss[j].Name == "$unionWith" {
					found = -2
				}
			}
			if found != -1 {
				break
			}
		}
		if found < 0 {
			continue
		}
		n, _ := ss[found].Spec["limit"].(int)
		spec := map[string]any{"sortKey": ss[i].Spec, "limit": n + skips}
		ss[i] = st("$sort", spec)
		ss = append(ss[:found], ss[found+1:]...)
		applied++
	}
	return ss, applied
}

func r6LimitLimit(ss []Stage) ([]Stage, int) {
	applied := 0
	for i := 0; i+1 < len(ss); i++ {
		if ss[i].Name == "$limit" && ss[i+1].Name == "$limit" {
			a, _ := ss[i].Spec["limit"].(int)
			b, _ := ss[i+1].Spec["limit"].(int)
			if b < a {
				a = b
			}
			ss = append(ss[:i], append([]Stage{st("$limit", map[string]any{"limit": a})}, ss[i+2:]...)...)
			applied++
		}
	}
	return ss, applied
}

func r7SkipSkip(ss []Stage) ([]Stage, int) {
	applied := 0
	for i := 0; i+1 < len(ss); i++ {
		if ss[i].Name == "$skip" && ss[i+1].Name == "$skip" {
			a, _ := ss[i].Spec["skip"].(int)
			b, _ := ss[i+1].Spec["skip"].(int)
			ss = append(ss[:i], append([]Stage{st("$skip", map[string]any{"skip": a + b})}, ss[i+2:]...)...)
			applied++
		}
	}
	return ss, applied
}

func r8MatchMatch(ss []Stage) ([]Stage, int) {
	applied := 0
	for i := 0; i+1 < len(ss); i++ {
		if ss[i].Name == "$match" && ss[i+1].Name == "$match" {
			merged := map[string]any{"$and": []any{ss[i].Spec, ss[i+1].Spec}}
			ss = append(ss[:i], append([]Stage{st("$match", merged)}, ss[i+2:]...)...)
			applied++
		}
	}
	return ss, applied
}

// Optimize 先重排后合并(官方:coalescence 发生在重排之后),跑到不动点。
func Optimize(ss []Stage) []Stage {
	for round := 0; round < 40; round++ {
		changed := 0
		for _, f := range []func([]Stage) ([]Stage, int){r1Pushdown, r2SortMatch, r4ProjectionSkip} {
			var n int
			ss, n = f(ss)
			changed += n
		}
		for _, f := range []func([]Stage) ([]Stage, int){r5SortLimit, r6LimitLimit, r7SkipSkip, r8MatchMatch} {
			var n int
			ss, n = f(ss)
			changed += n
		}
		if changed == 0 {
			break
		}
	}
	return ss
}

