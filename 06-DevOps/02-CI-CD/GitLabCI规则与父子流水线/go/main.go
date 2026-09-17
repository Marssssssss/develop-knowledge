// 自检入口: 与 python/gitlab_check.py 对应的关键断言子集(Go 侧)。
//
// 运行: cd go && go run .
package main

import (
	"fmt"
	"math"
	"strings"
)

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

func bp(b bool) *bool { return &b }

func main() {
	fmt.Println("GitLab CI 语义自检(Go 对照)")

	vars := map[string]string{
		"CI_PIPELINE_SOURCE": "merge_request_event", "CI_COMMIT_BRANCH": "main",
		"CI_DEFAULT_BRANCH": "main", "CI_COMMIT_TITLE": "feat: x-draft",
		"CI_JOB_NAME": "regex-job1", "pattern": "/^ab.*/", "teststring": "abcde",
	}

	// ---------- rules 首次匹配 ----------
	rules := []Rule{
		{If: `$CI_PIPELINE_SOURCE == "merge_request_event"`, When: "manual", AllowFailure: bp(true)},
		{If: `$CI_PIPELINE_SOURCE == "schedule"`},
	}
	inc, attrs, err := SelectJob(rules, JobCtx{Variables: vars})
	check("MR 命中第 1 条", err == nil && inc && attrs.RuleIndex == 0, fmt.Sprint(err))
	check("第 1 条属性 when=manual/allow_failure=true",
		attrs.When == "manual" && attrs.AllowFailure, attrs.When)
	inc, attrs, _ = SelectJob(rules, JobCtx{Variables: map[string]string{"CI_PIPELINE_SOURCE": "schedule"}})
	check("计划流水线落到第 2 条", inc && attrs.RuleIndex == 1, "")
	check("第 2 条未写属性 -> 默认 on_success/false",
		attrs.When == "on_success" && !attrs.AllowFailure, attrs.When)
	inc, _, _ = SelectJob(rules, JobCtx{Variables: map[string]string{"CI_PIPELINE_SOURCE": "push"}})
	check("其余情况无 rule 命中 -> 不加入", !inc, "")

	excl := []Rule{
		{If: `$CI_PIPELINE_SOURCE == "merge_request_event"`, When: "never"},
		{When: "on_success"},
	}
	inc, _, _ = SelectJob(excl, JobCtx{Variables: vars})
	check("when: never 排除", !inc, "")
	inc, attrs, _ = SelectJob(excl, JobCtx{Variables: map[string]string{"CI_PIPELINE_SOURCE": "push"}})
	check("兜底 when: on_success 放行", inc && attrs.When == "on_success", "")

	inc, attrs, _ = SelectJob([]Rule{{When: "manual"}}, JobCtx{})
	check("rules 内 when: manual -> allow_failure 默认 false",
		inc && !attrs.AllowFailure, "")

	// ---------- rules:if ----------
	ev := func(src string) bool {
		b, e := EvalIf(src, vars)
		return e == nil && b
	}
	check("== 相等", ev(`$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH`), "")
	check("!= 不等", ev(`$CI_COMMIT_BRANCH != "dev"`), "")
	check("=~ 正则命中", ev(`$CI_COMMIT_TITLE =~ /-draft$/`), "")
	check("!~ 正则取反", ev(`$CI_COMMIT_TITLE !~ /^release/`), "")
	check("=~ 正则未命中为假", !ev(`$CI_COMMIT_TITLE =~ /^release/`), "")
	check("变量存正则 + =~ 变量", ev(`$teststring =~ $pattern`), "")
	check("正则内变量不展开", !ev(`$CI_JOB_NAME =~ /$pattern/`), "")
	check("未定义变量按空串", ev(`$UNDEFINED == ""`), "")
	check("括号 + && + ||",
		ev(`($CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH || $CI_COMMIT_BRANCH == "develop") && $CI_JOB_NAME`), "")
	_, err = EvalIf(`$A == $B == $C`, vars)
	check("比较链是创建期错误", err != nil, "")

	// ---------- changes / exists ----------
	push := JobCtx{PipelineSource: "push", ChangedPaths: []string{"Dockerfile", "src/a.py"}}
	inc, _, _ = SelectJob([]Rule{Changes: []string{"Dockerfile", "docker/scripts/**/*"}}, push)
	check("push 流水线 changes 命中", inc, "")
	inc, _, _ = SelectJob([]Rule{Changes: []string{"**/*.rs"}}, push)
	check("push 流水线 changes 未命中", !inc, "")
	for _, src := range []string{"tag", "schedule", "web", "api"} {
		inc, _, _ = SelectJob([]Rule{Changes: []string{"**/*.rs"}},
			JobCtx{PipelineSource: src})
		check("非推送类流水线("+src+") changes 恒真", inc, "")
	}
	inc, _, _ = SelectJob([]Rule{Changes: []string{"**/*.rs"}},
		JobCtx{PipelineSource: "push", IsNewBranch: true})
	check("新分支 changes 恒真", inc, "")
	inc, _, _ = SelectJob([]Rule{{Changes: []string{"a.py"}, ChangesTo: "refs/heads/main"}},
		JobCtx{PipelineSource: "push", ChangedPaths: []string{"a.py", "README.md"},
			ChangedSince: map[string][]string{"refs/heads/main": {"a.py"}}})
	check("compare_to 换基线后命中", inc, "")
	inc, _, _ = SelectJob([]Rule{Exists: []string{"go.mod"}},
		JobCtx{Files: []string{"go.mod", "cmd/main.go"}})
	check("exists 命中", inc, "")
	inc, _, _ = SelectJob([]Rule{Exists: []string{"cmd/**/*.go"}},
		JobCtx{Files: []string{"go.mod", "cmd/main.go"}})
	check("exists 的 **/ 匹配零个目录段", inc, "")
	inc, _, _ = SelectJob([]Rule{Exists: []string{"Cargo.toml"}},
		JobCtx{Files: []string{"go.mod"}})
	check("exists 未命中", !inc, "")

	// ---------- glob 语义 ----------
	check("**/ 匹配零目录: cmd/main.go", GlobMatch("cmd/**/*.go", "cmd/main.go"), "")
	check("**/ 匹配一层目录: cmd/a/b.go", GlobMatch("cmd/**/*.go", "cmd/a/b.go"), "")
	check("* 不跨 /: cmd/*.go 不匹配 cmd/a/b.go", !GlobMatch("cmd/*.go", "cmd/a/b.go"), "")
	check("? = 单个字符(GitLab 是标准 glob)", GlobMatch("a?.go", "ab.go"), "")
	check("? 不等于量词: a?.go 不匹配 a.go", !GlobMatch("a?.go", "a.go"), "")

	// ---------- include ----------
	dup := map[string][]string{}
	list := make([]string, 0, MaxIncludes)
	for i := 0; i < MaxIncludes; i++ {
		list = append(list, "a.yml")
	}
	dup[".gitlab-ci.yml"] = list
	dup["a.yml"] = nil
	st, err := ResolveIncludes(dup, ".gitlab-ci.yml")
	check(fmt.Sprintf("重复 include 计入上限(恰好 %d 个合法)", MaxIncludes),
		err == nil && st.Count == MaxIncludes, fmt.Sprint(err))
	check(fmt.Sprintf("%d 个 include 恰好用满 30 秒预算", MaxIncludes),
		math.Abs(st.Seconds-IncludeBudget) < 1e-9, fmt.Sprint(st.Seconds))
	over := map[string][]string{".gitlab-ci.yml": append(list, "a.yml"), "a.yml": nil}
	_, err = ResolveIncludes(over, ".gitlab-ci.yml")
	check("超过上限报错", err != nil, "")

	nested := map[string][]string{
		".gitlab-ci.yml": {"l1.yml"}, "l1.yml": {"l2.yml"}, "l2.yml": nil,
	}
	st, err = ResolveIncludes(nested, ".gitlab-ci.yml")
	check("嵌套 include 计数含全部层级", err == nil && st.Count == 2, fmt.Sprint(err))
	check("嵌套 include 记录最大深度 2", st.MaxDepth == 2, fmt.Sprint(st.MaxDepth))

	// ---------- 下游流水线 ----------
	check("第 2 层子流水线合法(父子)", len(CheckChildTrigger(2, 2, 1, false)) == 0, "")
	errs := CheckChildTrigger(3, 1, 1, false)
	check("第 3 层被拒: 父子最多两层",
		len(errs) == 1 && strings.Contains(errs[0], "cannot trigger another level"), fmt.Sprint(errs))
	check("多项目流水线无嵌套限制", len(CheckChildTrigger(9, 1, 1, true)) == 0, "")
	check("单个子流水线 4 个配置文件被拒", len(CheckChildTrigger(1, 4, 1, false)) == 1, "")
	check("下游流水线 1001 条被拒", len(CheckChildTrigger(1, 1, MaxDownstream+1, false)) == 1, "")
	check("子流水线 CI_PIPELINE_SOURCE = parent_pipeline",
		ChildPipelineSource(false) == "parent_pipeline", "")
	check("多项目下游 CI_PIPELINE_SOURCE = pipeline",
		ChildPipelineSource(true) == "pipeline", "")

	fmt.Printf("\n断言 %d 通过 / %d 失败\n", pass, fail)
	if fail > 0 {
		for _, f := range failed {
			fmt.Println("  - " + f)
		}
	}
}
