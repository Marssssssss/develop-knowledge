// GitLab CI rules / include / 下游流水线 语义模型(Go 对照实现)。
//
// 语义依据同 python/gitlab_pipeline.py 头部的官方文档引用。
package main

import (
	"fmt"
	"regexp"
	"strings"
)

const (
	MaxIncludes     = 150 // 官方: 每流水线默认最多 150 个 include(含嵌套, 重复计入)
	IncludeCostSec  = 0.2 // 本 demo 口径: 每个 include 解析按 200ms 计
	IncludeBudget   = 30.0
	MaxChildConfigs = 3
	MaxDownstream   = 1000
	MaxChildDepth   = 2
)

var globCache = map[string]*regexp.Regexp{}

// GlobToRegex 把 GitLab 的 glob 编译成正则: `**/` 匹配零个或多个目录段。
// 不能用 path.Match —— 它不支持 `**`; 也不能用 filepath.Match 的 `**` 语义(不存在)。
func GlobToRegex(pat string) *regexp.Regexp {
	if re, ok := globCache[pat]; ok {
		return re
	}
	var b strings.Builder
	b.WriteString("^")
	for i := 0; i < len(pat); {
		c := pat[i]
		if c == '*' {
			if i+1 < len(pat) && pat[i+1] == '*' {
				if i+2 < len(pat) && pat[i+2] == '/' {
					b.WriteString("(?:.*/)?")
					i += 3
				} else {
					b.WriteString(".*")
					i += 2
				}
			} else {
				b.WriteString("[^/]*")
				i++
			}
			continue
		}
		if c == '?' {
			b.WriteString("[^/]")
			i++
			continue
		}
		b.WriteString(regexp.QuoteMeta(string(c)))
		i++
	}
	b.WriteString("$")
	re := regexp.MustCompile(b.String())
	globCache[pat] = re
	return re
}

// GlobMatch 单条 glob 匹配。
func GlobMatch(pat, path string) bool { return GlobToRegex(pat).MatchString(path) }

// GlobAny 任意一条模式命中。
func GlobAny(pats []string, values []string) bool {
	for _, v := range values {
		for _, p := range pats {
			if GlobMatch(p, v) {
				return true
			}
		}
	}
	return false
}

// ---------------------------------------------------------------- rules:if

// 注意: `!~` 必须排在 `!=` 之前, 否则会被切成 `!` + `=`.
var ifTokRe = regexp.MustCompile(
	`\s*(\(|\)|&&|\|\||=~|!~|==|!=|\$?[A-Za-z_][A-Za-z0-9_]*|/[^/]*/|'[^']*'|"[^"]*")`)

func tokenizeIf(expr string) ([]string, error) {
	var out []string
	for i := 0; i < len(expr); {
		m := ifTokRe.FindStringSubmatchIndex(expr[i:])
		if m == nil || m[0] != 0 {
			return nil, fmt.Errorf("if 表达式无法解析: %q", expr[i:])
		}
		out = append(out, expr[i+m[2]:i+m[3]])
		i += m[1]
	}
	return out, nil
}

type ifParser struct {
	toks []string
	pos  int
	vars map[string]string
}

func (p *ifParser) peek() string {
	if p.pos < len(p.toks) {
		return p.toks[p.pos]
	}
	return ""
}

func (p *ifParser) take() string { t := p.peek(); p.pos++; return t }

func (p *ifParser) atom() (string, error) {
	t := p.take()
	switch {
	case t == "(":
		v, err := p.orExpr()
		if err != nil {
			return "", err
		}
		if p.take() != ")" {
			return "", fmt.Errorf("括号不匹配")
		}
		return v, nil
	case t == "":
		return "", fmt.Errorf("表达式意外结束")
	case strings.HasPrefix(t, "'") || strings.HasPrefix(t, `"`):
		return t[1 : len(t)-1], nil
	case strings.HasPrefix(t, "/"):
		return t, nil // 正则字面量原样返回(绝不能当变量查表)
	case t == "true" || t == "false":
		return t, nil
	case strings.HasPrefix(t, "$"):
		return p.vars[t[1:]], nil
	}
	return p.vars[t], nil
}

func matchesIf(left, op, right string) bool {
	switch op {
	case "==":
		return left == right
	case "!=":
		return left != right
	}
	rx := right
	if len(rx) >= 2 && strings.HasPrefix(rx, "/") && strings.HasSuffix(rx, "/") {
		rx = rx[1 : len(rx)-1] // 正则内的变量**不展开**(官方明确)
	} else {
		rx = regexp.QuoteMeta(rx)
	}
	re, err := regexp.Compile(rx)
	if err != nil {
		return false
	}
	hit := re.MatchString(left)
	if op == "=~" {
		return hit
	}
	return !hit
}

func (p *ifParser) cmpExpr() (string, error) {
	left, err := p.atom()
	if err != nil {
		return "", err
	}
	if op := p.peek(); op == "==" || op == "!=" || op == "=~" || op == "!~" {
		p.pos++
		right, err := p.atom()
		if err != nil {
			return "", err
		}
		if matchesIf(left, op, right) {
			left = "true"
		} else {
			left = "false"
		}
	}
	if op := p.peek(); op == "==" || op == "!=" || op == "=~" || op == "!~" {
		return "", fmt.Errorf("不支持比较链(A == B == C)")
	}
	return left, nil
}

func (p *ifParser) andExpr() (string, error) {
	left, err := p.cmpExpr()
	if err != nil {
		return "", err
	}
	for p.peek() == "&&" {
		p.pos++
		right, err := p.cmpExpr()
		if err != nil {
			return "", err
		}
		if left == "true" && right == "true" {
			left = "true"
		} else {
			left = "false"
		}
	}
	return left, nil
}

func (p *ifParser) orExpr() (string, error) {
	left, err := p.andExpr()
	if err != nil {
		return "", err
	}
	for p.peek() == "||" {
		p.pos++
		right, err := p.andExpr()
		if err != nil {
			return "", err
		}
		if left == "true" || right == "true" {
			left = "true"
		} else {
			left = "false"
		}
	}
	return left, nil
}

// EvalIf 求值 rules:if 表达式; 未定义变量按空串。
func EvalIf(expr string, vars map[string]string) (bool, error) {
	toks, err := tokenizeIf(expr)
	if err != nil {
		return false, err
	}
	p := &ifParser{toks: toks, vars: vars}
	v, err := p.orExpr()
	if err != nil {
		return false, err
	}
	if p.pos != len(p.toks) {
		return false, fmt.Errorf("if 表达式有多余 token: %v", p.toks[p.pos:])
	}
	return v == "true", nil
}

