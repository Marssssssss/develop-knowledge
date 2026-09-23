// Package main 转写 scaffold.py：软件模板的表达式求值与步骤执行（Go 侧）。
package main

import (
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

// TemplateError 表示模板错误（缺字段、未定义变量、非法时长等）。
var TemplateError = errors.New("template error")

// 两种 apiVersion：v1beta3 是 scaffolder 自己的 group。
var templateAPIVersions = []string{"backstage.io/v1beta2", "scaffolder.backstage.io/v1beta3"}

var (
	typedExprRe = regexp.MustCompile(`\$\{\{(.+?)\}\}`)
	njkExprRe   = regexp.MustCompile(`(?!\$)\{\{(.+?)\}\}`)
	durationRe  = regexp.MustCompile(`^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$`)
	_           = njkExprRe
)

// Context 是模板求值上下文：parameters / steps / values。
type Context map[string]any

// SplitPath 把 `steps['publish'].output.remoteUrl` 切成 token。
func SplitPath(path string) []string {
	var tokens []string
	var buf strings.Builder
	for i := 0; i < len(path); {
		switch path[i] {
		case '.':
			if buf.Len() > 0 {
				tokens = append(tokens, buf.String())
				buf.Reset()
			}
			i++
		case '[':
			if buf.Len() > 0 {
				tokens = append(tokens, buf.String())
				buf.Reset()
			}
			end := strings.Index(path[i:], "]")
			if end < 0 {
				tokens = append(tokens, path[i+1:])
				i = len(path)
				continue
			}
			tokens = append(tokens, strings.Trim(path[i+1:i+end], "'\""))
			i += end + 1
		default:
			buf.WriteByte(path[i])
			i++
		}
	}
	if buf.Len() > 0 {
		tokens = append(tokens, buf.String())
	}
	return tokens
}

// Lookup 按下标/点号路径取值，未定义即报错。
func Lookup(ctx Context, path string) (any, error) {
	var cur any = map[string]any(ctx)
	for _, token := range SplitPath(strings.TrimSpace(path)) {
		m, ok := cur.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%w: cannot descend at %q", TemplateError, path)
		}
		v, ok := m[token]
		if !ok {
			return nil, fmt.Errorf("%w: undefined variable %q (missing %q)", TemplateError, path, token)
		}
		cur = v
	}
	return cur, nil
}

func stringify(v any) string {
	switch t := v.(type) {
	case bool:
		if t {
			return "True"
		}
		return "False"
	case nil:
		return ""
	default:
		return fmt.Sprint(t)
	}
}

// RenderTyped 处理 `${{ }}`：整串表达式保留类型，混排时拼接成字符串。
func RenderTyped(text string, ctx Context) (any, error) {
	stripped := strings.TrimSpace(text)
	if m := typedExprRe.FindStringSubmatch(stripped); m != nil && m[0] == stripped {
		return Lookup(ctx, m[1])
	}
	var firstErr error
	out := typedExprRe.ReplaceAllStringFunc(text, func(s string) string {
		m := typedExprRe.FindStringSubmatch(s)
		v, err := Lookup(ctx, m[1])
		if err != nil && firstErr == nil {
			firstErr = err
		}
		return stringify(v)
	})
	if firstErr != nil {
		return nil, firstErr
	}
	return out, nil
}

// Template 是一条黄金路径模板。
type Template struct {
	APIVersion string
	Name       string
	Spec       map[string]any
}

// ValidateTemplate 校验 apiVersion 与 spec 必填字段。
func ValidateTemplate(t Template) []string {
	var errs []string
	ok := false
	for _, v := range templateAPIVersions {
		if v == t.APIVersion {
			ok = true
		}
	}
	if !ok {
		errs = append(errs, fmt.Sprintf("apiVersion must be one of %v", templateAPIVersions))
	}
	if t.Name == "" {
		errs = append(errs, "metadata.name is required")
	}
	for _, key := range []string{"type", "parameters", "steps"} {
		if _, ok := t.Spec[key]; !ok {
			errs = append(errs, fmt.Sprintf("spec.%s is required", key))
		}
	}
	return errs
}

// ParseDuration 解析 ISO 8601 时长为秒。
func ParseDuration(text string) (float64, error) {
	m := durationRe.FindStringSubmatch(text)
	if m == nil || text == "P" || text == "PT" {
		return 0, fmt.Errorf("%w: invalid ISO 8601 duration %q", TemplateError, text)
	}
	num := func(s string) float64 {
		if s == "" {
			return 0
		}
		v, _ := strconv.ParseFloat(s, 64)
		return v
	}
	return num(m[1])*86400 + num(m[2])*3600 + num(m[3])*60 + num(m[4]), nil
}

// StepResult 记录一步的执行结果。
type StepResult struct {
	ID     string
	Status string
	Output map[string]any
}

// Action 是一个 scaffolder 动作。
type Action struct {
	ID         string
	Handler    func(map[string]any) map[string]any
	Required   []string
	SupportsDryRun bool
}

// Execute 按序执行步骤（简化版：不支持 each 迭代）。
func Execute(t Template, params map[string]any, actions map[string]Action, dryRun bool) (Context, []StepResult, error) {
	if errs := ValidateTemplate(t); len(errs) > 0 {
		return nil, nil, fmt.Errorf("%w: %s", TemplateError, strings.Join(errs, "; "))
	}
	ctx := Context{"parameters": params, "steps": map[string]any{}}
	steps, _ := t.Spec["steps"].([]any)
	var results []StepResult

	for _, raw := range steps {
		step, ok := raw.(map[string]any)
		if !ok {
			return nil, nil, fmt.Errorf("%w: step must be an object", TemplateError)
		}
		action, ok := actions[step["action"].(string)]
		if !ok {
			return nil, nil, fmt.Errorf("%w: unknown action %v", TemplateError, step["action"])
		}
		if cond, ok := step["if"]; ok {
			v, err := RenderTyped(cond.(string), ctx)
			if err != nil {
				return nil, nil, err
			}
			if !truthy(v) {
				ctx["steps"].(map[string]any)[step["id"].(string)] = map[string]any{"output": map[string]any{}}
				results = append(results, StepResult{step["id"].(string), "skipped", map[string]any{}})
				continue
			}
		}
		input := map[string]any{}
		if rawInput, ok := step["input"].(map[string]any); ok {
			for k, v := range rawInput {
				rv, err := RenderTyped(fmt.Sprint(v), ctx)
				if err != nil {
					return nil, nil, err
				}
				input[k] = rv
			}
		}
		for _, name := range action.Required {
			if _, ok := input[name]; !ok {
				return nil, nil, fmt.Errorf("%w: action %s requires input %q", TemplateError, action.ID, name)
			}
		}
		output := action.Handler(input)
		if output == nil {
			output = map[string]any{}
		}
		ctx["steps"].(map[string]any)[step["id"].(string)] = map[string]any{"output": output}
		status := "completed"
		if dryRun && !action.SupportsDryRun {
			status = "dry-run"
		}
		results = append(results, StepResult{step["id"].(string), status, output})
	}
	return ctx, results, nil
}

func truthy(v any) bool {
	switch t := v.(type) {
	case bool:
		return t
	case string:
		s := strings.ToLower(strings.TrimSpace(t))
		return s == "true" || s == "yes" || s == "1"
	default:
		return v != nil
	}
}
