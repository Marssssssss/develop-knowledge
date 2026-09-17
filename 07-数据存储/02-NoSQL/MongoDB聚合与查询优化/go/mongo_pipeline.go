// mongo_pipeline.go — MongoDB 聚合管道阶段模型与字段影响(Go 实现,同包 4 文件)
//
// 权威来源:
//   - MongoDB manual: Aggregation Pipeline Optimization
//     https://www.mongodb.com/docs/manual/core/aggregation-pipeline-optimization/
//   - MongoDB manual: The ESR (Equality, Sort, Range) Guideline
//     https://www.mongodb.com/docs/manual/tutorial/equality-sort-range-guideline/
//   - MongoDB manual: Aggregation Pipeline Limits(1000 阶段 / 100 MB / 16 MiB)
//
// 文件分工(同一 package main,故拆多文件不放宽 300 行硬约束之外的东西):
//   mongo_pipeline.go   阶段模型 + 字段影响(本文件)
//   mongo_optimizer.go  序列重排 R1/R2/R4 + 合并 R5~R8 + Optimize
//   mongo_esr.go        ESR 谓词分类 + 索引选择 + 结构校验
//   mongo_pipeline_check.go  自检
//
// 口径差异(已知):R3($redact+$match)与 R9($lookup+$unwind)只在 Python 版实现
// (python/mongo_optimizer.py),不是"官方只有这些规则"。
//
// 运行: go run mongo_pipeline.go mongo_optimizer.go mongo_esr.go mongo_pipeline_check.go
package main

import (
	"fmt"
	"sort"
	"strings"
)

// ---------------------------------------------------------------- 阶段模型

// Stage 是一个聚合阶段,Name 形如 "$match",Spec 为参数文档。
type Stage struct {
	Name string
	Spec map[string]any
}

func st(name string, spec map[string]any) Stage { return Stage{name, spec} }

func isProjection(s Stage) bool {
	return s.Name == "$addFields" || s.Name == "$project" || s.Name == "$set" || s.Name == "$unset"
}

func countChanging(name string) bool {
	switch name {
	case "$unwind", "$group", "$bucket", "$bucketAuto":
		return true
	}
	return false
}

func dumpSpec(spec map[string]any) string {
	keys := make([]string, 0, len(spec))
	for k := range spec {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, k+"="+fmt.Sprint(spec[k]))
	}
	return "{" + strings.Join(parts, ",") + "}"
}

func dumpStages(ss []Stage) string {
	parts := make([]string, len(ss))
	for i, s := range ss {
		parts[i] = s.Name + " " + dumpSpec(s.Spec)
	}
	return strings.Join(parts, " | ")
}

// ---------------------------------------------------------------- 字段影响

// FieldEffect 描述一个投影阶段对顶层字段的影响。
type FieldEffect struct {
	Computed map[string]bool
	Removed  map[string]bool
}

// Affects 表示"过滤器跨越该阶段会改变语义"。
func (e FieldEffect) Affects(f string) bool { return e.Computed[f] || e.Removed[f] }

func fieldEffect(s Stage) FieldEffect {
	eff := FieldEffect{Computed: map[string]bool{}, Removed: map[string]bool{}}
	switch s.Name {
	case "$addFields", "$set":
		for k := range s.Spec {
			eff.Computed[k] = true
		}
	case "$unset":
		if list, ok := s.Spec["$unset"].([]string); ok {
			for _, k := range list {
				eff.Removed[k] = true
			}
		}
	case "$project":
		for k, v := range s.Spec {
			switch vv := v.(type) {
			case bool:
				if !vv {
					eff.Removed[k] = true
				}
			case int:
				if vv == 0 {
					eff.Removed[k] = true
				}
			default:
				eff.Computed[k] = true
			}
		}
	}
	return eff
}

// filterFields 取过滤器文档引用到的顶层字段(展开顶层 $and)。
func filterFields(spec map[string]any) map[string]bool {
	out := map[string]bool{}
	var walk func(map[string]any)
	walk = func(m map[string]any) {
		for k, v := range m {
			if k == "$and" {
				if list, ok := v.([]any); ok {
					for _, sub := range list {
						if sm, ok2 := sub.(map[string]any); ok2 {
							walk(sm)
						}
					}
				}
				continue
			}
			if strings.HasPrefix(k, "$") {
				continue
			}
			out[k] = true
		}
	}
	walk(spec)
	return out
}

// namedFilter 是一个拆出来的单键过滤器(Label 仅用于调试)。
type namedFilter struct {
	Label string
	Spec  map[string]any
}

func splitMatchFilters(spec map[string]any) []namedFilter {
	out := []namedFilter{}
	keys := make([]string, 0, len(spec))
	for k := range spec {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		if k == "$and" {
			if list, ok := spec[k].([]any); ok {
				for i, sub := range list {
					if sm, ok2 := sub.(map[string]any); ok2 {
						out = append(out, namedFilter{fmt.Sprintf("%s[%d]", k, i), sm})
					}
				}
				continue
			}
		}
		out = append(out, namedFilter{k, map[string]any{k: spec[k]}})
	}
	return out
}

func mergeFilters(fs []map[string]any) map[string]any {
	merged := map[string]any{}
	for _, f := range fs {
		for k, v := range f {
			merged[k] = v
		}
	}
	return merged
}

