// Jenkins 声明式 Pipeline 语义模型(Go 对照实现)。
//
// 语义依据同 python/jenkins_pipeline.py 头部的官方文档引用。
// 运行: cd go && go run .
package main

import (
	"fmt"
	"strings"
)

// PostOrder 官方给出的 post 条件执行顺序(逐字顺序, 不可调整)。
var PostOrder = []string{"always", "changed", "fixed", "regression", "aborted",
	"failure", "success", "unstable", "unsuccessful", "cleanup"}

var statuses = map[string]bool{"success": true, "failure": true,
	"unstable": true, "aborted": true}

// StageAllowedOptions stage 级 options 的白名单。
var StageAllowedOptions = []string{"retry", "timeout", "timestamps", "skipDefaultCheckout"}

// PostConditionRuns 单个 post 条件是否触发。
func PostConditionRuns(cond, status, prev string) bool {
	switch cond {
	case "always", "cleanup":
		return true
	case "changed":
		return status != prev
	case "fixed":
		return status == "success" && (prev == "failure" || prev == "unstable")
	case "regression":
		return (status == "failure" || status == "unstable" || status == "aborted") &&
			prev == "success"
	case "unsuccessful":
		return status != "success"
	}
	return cond == status // aborted / failure / success / unstable
}

// RunPost 按**官方固定顺序**返回实际执行的 post 条件(与声明顺序无关)。
func RunPost(declared []string, status, prev string) ([]string, error) {
	if !statuses[status] {
		return nil, fmt.Errorf("未知构建状态: %s", status)
	}
	set := map[string]bool{}
	for _, d := range declared {
		set[d] = true
	}
	var out []string
	for _, c := range PostOrder {
		if set[c] && PostConditionRuns(c, status, prev) {
			out = append(out, c)
		}
	}
	return out, nil
}

// TimesOut 返回 (是否超时, 计入 timeout 的秒数)。
// 顶层 agent: 分配时间不计入; stage 级 agent: 分配时间计入。
func TimesOut(scope string, allocSeconds, workSeconds, limitSeconds float64) (bool, float64, error) {
	if scope != "top-level" && scope != "stage" {
		return false, 0, fmt.Errorf("scope 只能是 top-level / stage")
	}
	counted := workSeconds
	if scope == "stage" {
		counted = allocSeconds + workSeconds
	}
	return counted > limitSeconds, counted, nil
}

// StageEvalOrder 返回 stage 内各段的实际求值顺序。
// 基线 options -> input -> agent -> when; 三个开关按 beforeOptions > beforeInput >
// beforeAgent 的优先级把 when 往前提。
func StageEvalOrder(beforeOptions, beforeInput, beforeAgent bool) []string {
	switch {
	case beforeOptions:
		return []string{"when", "options", "input", "agent"}
	case beforeInput:
		return []string{"options", "when", "input", "agent"}
	case beforeAgent:
		return []string{"options", "input", "when", "agent"}
	}
	return []string{"options", "input", "agent", "when"}
}

// WhenPriorityRank 生效的 before* 开关权重(越大越优先)。
func WhenPriorityRank(beforeOptions, beforeInput, beforeAgent bool) int {
	switch {
	case beforeOptions:
		return 3
	case beforeInput:
		return 2
	case beforeAgent:
		return 1
	}
	return 0
}

// ValidateStageBody 校验"有且仅有一个"与 parallel/matrix 不可嵌套。
func ValidateStageBody(hasSteps, hasStages, hasParallel, hasMatrix, parentIsParOrMat bool) []string {
	var errs []string
	n := 0
	for _, b := range []bool{hasSteps, hasStages, hasParallel, hasMatrix} {
		if b {
			n++
		}
	}
	if n != 1 {
		errs = append(errs, fmt.Sprintf(
			"一个 stage 必须有且仅有一个 steps/stages/parallel/matrix, 实际 %d 个", n))
	}
	if parentIsParOrMat && (hasParallel || hasMatrix) {
		errs = append(errs, "parallel/matrix 块内的 stage 不能再嵌套 parallel/matrix")
	}
	return errs
}

// ValidateOptionsScope 校验 options 的作用域白名单。
func ValidateOptionsScope(scope string, names []string) []string {
	var errs []string
	allowed := map[string]bool{}
	for _, a := range StageAllowedOptions {
		allowed[a] = true
	}
	for _, n := range names {
		if scope == "stage" && !allowed[n] {
			errs = append(errs, fmt.Sprintf("stage 级 options 不允许 %s(仅限 %s)",
				n, strings.Join(StageAllowedOptions, "/")))
		}
	}
	return errs
}

// RunParallel 返回 (实际执行的 stage, 被中止的 stage)。
func RunParallel(stages []string, results map[string]string,
	failFast, globalFailFast bool) ([]string, []string) {
	var executed, aborted []string
	stopped := false
	for _, s := range stages {
		if stopped {
			aborted = append(aborted, s)
			continue
		}
		executed = append(executed, s)
		if results[s] == "failure" && (failFast || globalFailFast) {
			stopped = true
		}
	}
	return executed, aborted
}

// ExcludeClause 是一个 exclude 里的单个轴子句。
type ExcludeClause struct {
	Values    []string
	NotValues []string
}

// MatrixCells 按官方 axis/exclude 语义生成静态 cell 集合。
func MatrixCells(axes [][2]interface{}, excludes []map[string]ExcludeClause) []map[string]string {
	// 展开笛卡尔积
	cells := []map[string]string{{}}
	for _, ax := range axes {
		name := ax[0].(string)
		vals := ax[1].([]string)
		var next []map[string]string
		for _, base := range cells {
			for _, v := range vals {
				c := map[string]string{}
				for k, val := range base {
					c[k] = val
				}
				c[name] = v
				next = append(next, c)
			}
		}
		cells = next
	}
	for _, ex := range excludes {
		var keep []map[string]string
		for _, c := range cells {
			hit := true
			for axisName, clause := range ex {
				if clause.Values != nil && !contains(clause.Values, c[axisName]) {
					hit = false
				}
				if clause.NotValues != nil && contains(clause.NotValues, c[axisName]) {
					hit = false
				}
			}
			if !hit {
				keep = append(keep, c)
			}
		}
		cells = keep
	}
	return cells
}

func contains(xs []string, v string) bool {
	for _, x := range xs {
		if x == v {
			return true
		}
	}
	return false
}

