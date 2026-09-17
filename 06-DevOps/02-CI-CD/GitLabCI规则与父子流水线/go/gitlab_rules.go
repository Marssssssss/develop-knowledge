// GitLab CI `rules` 求值、include 展开与下游流水线约束(与 gitlab_pipeline.go 同包)。
// 拆自原 382 行的单文件, 以满足 OPTIMIZATION.md §1.1 的"单源文件 ≤ 300 行"。
package main

import "fmt"

// ---------------------------------------------------------------- rules

// Rule 是一条 rules 条目。
type Rule struct {
	If           string
	Changes      []string
	ChangesTo    string
	Exists       []string
	When         string
	AllowFailure *bool
}

// JobCtx 是求值上下文。
type JobCtx struct {
	PipelineSource string
	IsNewBranch    bool
	ChangedPaths   []string
	ChangedSince   map[string][]string
	Files          []string
	Variables      map[string]string
}

// Attrs 是命中 rule 后生效的属性。
type Attrs struct {
	RuleIndex    int
	When         string
	AllowFailure bool
}

var pushSources = map[string]bool{"push": true, "merge_request_event": true}

// SelectJob 返回 (是否加入流水线, 生效属性)。无 rule 命中 -> 不加入。
func SelectJob(rules []Rule, ctx JobCtx) (bool, Attrs, error) {
	for i, r := range rules {
		if r.If != "" {
			ok, err := EvalIf(r.If, ctx.Variables)
			if err != nil {
				return false, Attrs{}, err
			}
			if !ok {
				continue
			}
		}
		if len(r.Changes) > 0 {
			if !pushSources[ctx.PipelineSource] || ctx.IsNewBranch {
				// 官方: 非推送类流水线 changes 恒真; 新分支无基线亦恒真
			} else {
				paths := ctx.ChangedPaths
				if r.ChangesTo != "" {
					if v, ok := ctx.ChangedSince[r.ChangesTo]; ok {
						paths = v
					}
				}
				if !GlobAny(r.Changes, paths) {
					continue
				}
			}
		}
		if len(r.Exists) > 0 && !GlobAny(r.Exists, ctx.Files) {
			continue
		}
		when := r.When
		if when == "" {
			when = "on_success"
		}
		if when == "never" {
			return false, Attrs{RuleIndex: i, When: "never"}, nil
		}
		allow := false // 官方: rules 内 when: manual 的 allow_failure 默认 false
		if r.AllowFailure != nil {
			allow = *r.AllowFailure
		}
		return true, Attrs{RuleIndex: i, When: when, AllowFailure: allow}, nil
	}
	return false, Attrs{RuleIndex: -1, When: "never"}, nil
}

// ---------------------------------------------------------------- include

// IncludeStat 统计 include 展开规模。
type IncludeStat struct {
	Count    int
	Seconds  float64
	MaxDepth int
}

// ResolveIncludes 展开 include 图(深度优先), 超限即报错。
func ResolveIncludes(files map[string][]string, root string) (IncludeStat, error) {
	st := IncludeStat{}
	var walk func(path string, depth int) error
	walk = func(path string, depth int) error {
		if depth > st.MaxDepth {
			st.MaxDepth = depth
		}
		for _, inc := range files[path] {
			st.Count++
			st.Seconds += IncludeCostSec
			if st.Count > MaxIncludes {
				return fmt.Errorf("include 数超过上限 %d(含嵌套, 重复计入)", MaxIncludes)
			}
			if st.Seconds > IncludeBudget+1e-9 {
				return fmt.Errorf("include 解析超过 %.0f 秒时限", IncludeBudget)
			}
			if _, ok := files[inc]; ok {
				if err := walk(inc, depth+1); err != nil {
					return err
				}
			}
		}
		return nil
	}
	if err := walk(root, 0); err != nil {
		return st, err
	}
	return st, nil
}

// ---------------------------------------------------------------- 下游流水线

// CheckChildTrigger 返回约束违规列表(空 = 合法)。
func CheckChildTrigger(depth, nConfigs, downstream int, crossProject bool) []string {
	var errs []string
	if !crossProject && depth > MaxChildDepth {
		errs = append(errs, fmt.Sprintf(
			"父子流水线最多 %d 层: cannot trigger another level of child pipelines", MaxChildDepth))
	}
	if nConfigs > MaxChildConfigs {
		errs = append(errs, fmt.Sprintf("单个子流水线最多拼 %d 个配置文件", MaxChildConfigs))
	}
	if downstream > MaxDownstream {
		errs = append(errs, fmt.Sprintf("层级中下游流水线超过 %d 条", MaxDownstream))
	}
	return errs
}

// ChildPipelineSource 子流水线里 $CI_PIPELINE_SOURCE 的取值。
func ChildPipelineSource(crossProject bool) string {
	if crossProject {
		return "pipeline"
	}
	return "parent_pipeline"
}
