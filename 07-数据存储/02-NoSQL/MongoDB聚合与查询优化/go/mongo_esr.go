// mongo_esr.go — ESR 谓词分类、索引选择与管道结构校验(同包 main)
//
// 权威来源:
//   - MongoDB manual: The ESR (Equality, Sort, Range) Guideline
//     https://www.mongodb.com/docs/manual/tutorial/equality-sort-range-guideline/
//     * "Equality fields must come first."
//     * "To avoid in-memory sorts, put sort fields before range in the index."
//     * "$ne or $nin are range operators" / "$regex is a range operator"
//     * "$in ... fewer than 201 array elements ... similar to an equality predicate"
//   - MongoDB manual: Aggregation Pipeline Limits(1000 阶段上限)
package main

import "sort"

// ---------------------------------------------------------------- ESR 索引

const inEqualityThreshold = 201

func classify(value any) string {
	m, ok := value.(map[string]any)
	if !ok {
		return "equality"
	}
	if len(m) == 1 {
		if v, ok := m["$in"]; ok {
			if arr, ok2 := v.([]any); ok2 && len(arr) < inEqualityThreshold {
				return "equality"
			}
			return "range"
		}
		if _, ok := m["$eq"]; ok {
			return "equality"
		}
	}
	return "range"
}

// Index 是一个复合索引,Fields 顺序即索引键序。
type Index struct {
	Name   string
	Fields []string
}

// Analyze 结果:界、是否免内存排序、是否覆盖。
type Plan struct {
	Index  string
	Bounds []string
	SortOK bool
	Cover  bool
}

func Analyze(ix Index, query map[string]any, sortKeys []string, needed []string) Plan {
	kinds := map[string]string{}
	for _, f := range ix.Fields {
		if v, ok := query[f]; ok {
			kinds[f] = classify(v)
		}
	}
	eq := []string{}
	pos := 0
	for pos < len(ix.Fields) && kinds[ix.Fields[pos]] == "equality" {
		eq = append(eq, ix.Fields[pos])
		pos++
	}
	rest := ix.Fields[pos:]
	sortOK := len(sortKeys) == 0
	if len(sortKeys) > 0 && len(rest) >= len(sortKeys) {
		sortOK = true
		for i, f := range sortKeys {
			if rest[i] != f {
				sortOK = false
				break
			}
		}
	}
	rangeField := ""
	for _, f := range rest {
		if kinds[f] == "range" {
			inSort := false
			for _, s := range sortKeys {
				if s == f {
					inSort = true
				}
			}
			if !inSort {
				rangeField = f
				break
			}
		}
	}
	bounds := append([]string{}, eq...)
	if rangeField != "" {
		bounds = append(bounds, rangeField)
	}
	cover := true
	for _, n := range needed {
		found := n == "_id"
		for _, f := range ix.Fields {
			if f == n {
				found = true
			}
		}
		if !found {
			cover = false
			break
		}
	}
	if len(bounds) == 0 {
		return Plan{Index: "", Bounds: bounds, SortOK: sortOK, Cover: cover}
	}
	return Plan{Index: ix.Name, Bounds: bounds, SortOK: sortOK, Cover: cover}
}

// Choose 按(免内存排序 > 覆盖查询 > 名字)选一个可用索引。
func Choose(ixs []Index, query map[string]any, sortKeys, needed []string) Plan {
	plans := []Plan{}
	for _, ix := range ixs {
		if p := Analyze(ix, query, sortKeys, needed); p.Index != "" {
			plans = append(plans, p)
		}
	}
	sort.SliceStable(plans, func(a, b int) bool {
		if plans[a].SortOK != plans[b].SortOK {
			return plans[a].SortOK
		}
		if plans[a].Cover != plans[b].Cover {
			return plans[a].Cover
		}
		return plans[a].Index < plans[b].Index
	})
	if len(plans) == 0 {
		return Plan{}
	}
	return plans[0]
}

// ---------------------------------------------------------------- 结构校验

const maxStages = 1000

func Validate(ss []Stage) []string {
	out := []string{}
	if len(ss) > maxStages {
		out = append(out, "too_many_stages")
	}
	for i, s := range ss {
		if (s.Name == "$out" || s.Name == "$merge") && i != len(ss)-1 {
			out = append(out, "not_last_stage: "+s.Name)
		}
		if s.Name == "$geoNear" && i != 0 {
			out = append(out, "not_first_stage: $geoNear")
		}
	}
	return out
}

