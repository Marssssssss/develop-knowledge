// 自检入口(与 python/jenkins_check.py 对应的关键断言子集)。
// 拆自原 384 行的单文件, 以满足 OPTIMIZATION.md §1.1 的"单源文件 ≤ 300 行"。
package main

import (
	"fmt"
	"sort"
	"strings"
)

// ------------------------------------------------------------------ 自检

var (
	pass, fail int
	failed     []string
)

func check(label string, cond bool, detail string) {
	if cond {
		pass++
		return
	}
	fail++
	failed = append(failed, label+" "+detail)
	fmt.Printf("  FAIL %s %s\n", label, detail)
}

func eq(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func sortedCells(cells []map[string]string) []string {
	var out []string
	for _, c := range cells {
		var ks []string
		for k, v := range c {
			ks = append(ks, k+"="+v)
		}
		sort.Strings(ks)
		out = append(out, strings.Join(ks, ","))
	}
	sort.Strings(out)
	return out
}

func main() {
	fmt.Println("Jenkins 声明式 Pipeline 语义自检(Go 对照)")

	check("post 顺序逐字一致",
		eq(PostOrder, []string{"always", "changed", "fixed", "regression", "aborted",
			"failure", "success", "unstable", "unsuccessful", "cleanup"}), "")

	checks := []struct {
		cond, status, prev string
		want               bool
	}{
		{"always", "failure", "failure", true},
		{"cleanup", "aborted", "success", true},
		{"success", "success", "failure", true},
		{"success", "unstable", "failure", false},
		{"failure", "failure", "success", true},
		{"failure", "unstable", "success", false},
		{"unstable", "unstable", "success", true},
		{"aborted", "aborted", "success", true},
		{"unsuccessful", "aborted", "success", true},
		{"unsuccessful", "success", "failure", false},
		{"changed", "success", "failure", true},
		{"changed", "failure", "failure", false},
		{"fixed", "success", "unstable", true},
		{"fixed", "success", "success", false},
		{"regression", "aborted", "success", true},
		{"regression", "failure", "failure", false},
	}
	for _, c := range checks {
		check("post "+c.cond+" @ "+c.status+"/"+c.prev,
			PostConditionRuns(c.cond, c.status, c.prev) == c.want, "")
	}

	got, err := RunPost([]string{"cleanup", "success", "always", "failure", "changed"},
		"failure", "success")
	check("执行顺序按官方顺序而非声明顺序",
		err == nil && eq(got, []string{"always", "changed", "failure", "cleanup"}), fmt.Sprint(got))
	got, _ = RunPost([]string{"cleanup", "regression", "fixed", "changed"}, "success", "failure")
	check("failure -> success: changed 与 fixed 触发且 fixed 在后",
		eq(got, []string{"changed", "fixed", "cleanup"}), fmt.Sprint(got))
	_, err = RunPost([]string{"always"}, "shiny", "")
	check("未知状态报错", err != nil, "")
	got, _ = RunPost(nil, "success", "success")
	check("空声明返回空列表", len(got) == 0, "")

	// agent / timeout
	ok, counted, _ := TimesOut("top-level", 600, 100, 300)
	check("顶层 agent: 分配 600s 不计入", !ok && counted == 100, fmt.Sprint(counted))
	ok, counted, _ = TimesOut("stage", 600, 100, 300)
	check("stage 级 agent: 分配 600s 计入", ok && counted == 700, fmt.Sprint(counted))
	ok, _, _ = TimesOut("stage", 0, 300, 300)
	check("恰好等于上限不算超时", !ok, "")
	_, _, err = TimesOut("pipeline", 1, 1, 1)
	check("非法 scope 报错", err != nil, "")

	// stage 求值时点
	check("默认顺序 options->input->agent->when",
		eq(StageEvalOrder(false, false, false), []string{"options", "input", "agent", "when"}), "")
	check("beforeAgent: when 提到 agent 之前",
		eq(StageEvalOrder(false, false, true), []string{"options", "input", "when", "agent"}), "")
	check("beforeInput: when 提到 input 之前",
		eq(StageEvalOrder(false, true, false), []string{"options", "when", "input", "agent"}), "")
	check("beforeOptions: when 提到最前",
		eq(StageEvalOrder(true, false, false), []string{"when", "options", "input", "agent"}), "")
	check("beforeOptions 全置位时仍最前",
		eq(StageEvalOrder(true, true, true), []string{"when", "options", "input", "agent"}), "")
	check("优先级 beforeOptions > beforeInput > beforeAgent",
		WhenPriorityRank(true, true, true) == 3 && WhenPriorityRank(false, true, true) == 2 &&
			WhenPriorityRank(false, false, true) == 1 && WhenPriorityRank(false, false, false) == 0, "")

	// 结构约束
	check("只有 steps 合法", len(ValidateStageBody(true, false, false, false, false)) == 0, "")
	check("steps + stages 非法", len(ValidateStageBody(true, true, false, false, false)) == 1, "")
	check("四者都无非法", len(ValidateStageBody(false, false, false, false, false)) == 1, "")
	check("parallel 块内的 stage 不能再 parallel",
		len(ValidateStageBody(false, false, true, false, true)) == 1, "")
	check("parallel 块内的 stage 可以只有 steps",
		len(ValidateStageBody(true, false, false, false, true)) == 0, "")
	check("stage 级允许 timeout", len(ValidateOptionsScope("stage", []string{"timeout"})) == 0, "")
	check("stage 级不允许 buildDiscarder",
		len(ValidateOptionsScope("stage", []string{"buildDiscarder"})) == 1, "")
	check("pipeline 级允许 buildDiscarder",
		len(ValidateOptionsScope("pipeline", []string{"buildDiscarder"})) == 0, "")
	check("stage 级不允许 parallelsAlwaysFailFast",
		len(ValidateOptionsScope("stage", []string{"parallelsAlwaysFailFast"})) == 1, "")

	// parallel / matrix
	stages := []string{"A", "B", "C"}
	ex, ab := RunParallel(stages, map[string]string{"A": "success", "B": "failure", "C": "success"}, true, false)
	check("failFast: B 失败后 C 被中止", eq(ex, []string{"A", "B"}) && eq(ab, []string{"C"}), "")
	ex, ab = RunParallel(stages, map[string]string{"A": "success", "B": "failure", "C": "success"}, false, false)
	check("未开 failFast: 三个 stage 全执行", eq(ex, stages) && len(ab) == 0, "")
	ex, ab = RunParallel(stages, map[string]string{"A": "success", "B": "failure", "C": "success"}, false, true)
	check("parallelsAlwaysFailFast() 等效", eq(ab, []string{"C"}), "")
	ex, _ = RunParallel(stages, map[string]string{"A": "unstable", "B": "unstable", "C": "unstable"}, true, false)
	check("全 unstable 不触发 failFast", eq(ex, stages), "")

	axes := [][2]interface{}{{"PLATFORM", []string{"linux", "mac", "windows"}},
		{"BROWSER", []string{"chrome", "edge", "firefox", "safari"}}}
	check("无 exclude 时 cell 数 = 12", len(MatrixCells(axes, nil)) == 12, "")
	ex1 := []map[string]ExcludeClause{
		{"PLATFORM": {Values: []string{"mac"}}, "BROWSER": {Values: []string{"safari"}}},
	}
	check("单条 exclude 去掉 1 个 cell", len(MatrixCells(axes, ex1)) == 11, "")
	ex2 := []map[string]ExcludeClause{
		{"PLATFORM": {Values: []string{"mac"}}, "BROWSER": {Values: []string{"edge"}}},
		{"PLATFORM": {NotValues: []string{"windows"}}, "BROWSER": {Values: []string{"edge"}}},
	}
	cells := MatrixCells(axes, ex2)
	check("notValues 排除 (linux|mac) x edge", len(cells) == 10, fmt.Sprint(len(cells)))
	hasWinEdge := false
	bad := false
	for _, c := range cells {
		if c["PLATFORM"] == "windows" && c["BROWSER"] == "edge" {
			hasWinEdge = true
		}
		if c["BROWSER"] == "edge" && (c["PLATFORM"] == "mac" || c["PLATFORM"] == "linux") {
			bad = true
		}
	}
	check("windows x edge 保留", hasWinEdge, "")
	check("被排除的 cell 不在结果里", !bad, "")
	check("axes 是静态集合: 两次调用结果一致",
		eq(sortedCells(MatrixCells(axes, ex2)), sortedCells(cells)), "")
	check("单轴 matrix", len(MatrixCells([][2]interface{}{{"JDK", []string{"8", "11", "17"}}}, nil)) == 3, "")

	fmt.Printf("\n断言 %d 通过 / %d 失败\n", pass, fail)
	if fail > 0 {
		for _, f := range failed {
			fmt.Println("  - " + f)
		}
	}
}
