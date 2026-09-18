package main

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
)

// Expr 是解析结果：一个选择器（可带函数、范围、修饰符、子查询）。
type Expr struct {
	Func     string
	Name     string
	Matchers []*Matcher
	RangeS   float64
	HasRange bool
	OffsetS  float64
	At       float64
	HasAt    bool
	AtFunc   string // "start()" / "end()" / ""
	SubRange float64
	HasSub   bool
	SubRes   float64
	HasRes   bool
}

// Parse 解析 PromQL 子集。语法：<func>(<selector>[range] mods)[subrange:resolution]
//
// 约束（官方 "Querying basics"）：
//   - offset / @ 必须紧跟选择器，故只允许出现在表达式文本末尾；
//   - 函数调用之后只允许出现**子查询后缀**，因此 `rate(x[5m]) offset 5m` 非法；
//   - @ 支持数值时间戳与 start() / end()。
func Parse(text string) (*Expr, error) {
	ex := &Expr{}
	t := strings.TrimSpace(text)
	tail := ""

	// 函数调用
	if i := strings.IndexByte(t, '('); i > 0 {
		name := strings.TrimSpace(t[:i])
		if rangeFuncs[name] {
			end, err := matchBrace(t, i, '(', ')')
			if err != nil {
				return nil, err
			}
			ex.Func = name
			tail = strings.TrimSpace(t[end+1:])
			t = strings.TrimSpace(t[i+1 : end])
		}
	}

	// 子查询后缀（直接写在选择器之后）
	if lb := strings.LastIndexByte(t, '['); lb >= 0 && strings.HasSuffix(t, "]") {
		inner := t[lb+1 : len(t)-1]
		if p := strings.LastIndex(inner, ":"); p >= 0 {
			d, err := ParseDuration(inner[:p])
			if err != nil {
				return nil, err
			}
			ex.SubRange, ex.HasSub = d, true
			if rest := strings.TrimSpace(inner[p+1:]); rest != "" {
				r, err := ParseDuration(rest)
				if err != nil {
					return nil, err
				}
				ex.SubRes, ex.HasRes = r, true
			}
			t = strings.TrimSpace(t[:lb])
		}
	}

	// 子查询后缀（写在函数调用之后）
	if tail != "" {
		if ex.HasSub || !strings.HasPrefix(tail, "[") || !strings.HasSuffix(tail, "]") {
			return nil, fmt.Errorf("函数调用后只允许子查询后缀，实得: %q", tail)
		}
		inner := tail[1 : len(tail)-1]
		p := strings.LastIndex(inner, ":")
		if p < 0 {
			return nil, fmt.Errorf("函数调用后只允许子查询后缀，实得: %q", tail)
		}
		d, err := ParseDuration(inner[:p])
		if err != nil {
			return nil, err
		}
		ex.SubRange, ex.HasSub = d, true
		if rest := strings.TrimSpace(inner[p+1:]); rest != "" {
			r, err := ParseDuration(rest)
			if err != nil {
				return nil, err
			}
			ex.SubRes, ex.HasRes = r, true
		}
	}

	// 修饰符 offset / @（可重复、顺序无关；必须紧贴选择器，即位于文本末尾）
	for {
		s := strings.TrimSpace(t)
		if i := strings.LastIndex(s, "offset"); i > 0 && s[i-1] == ' ' {
			cand := strings.TrimSpace(s[i+6:])
			neg := strings.HasPrefix(cand, "-")
			if neg {
				cand = cand[1:]
			}
			if d, err := ParseDuration(cand); err == nil {
				ex.OffsetS = d
				if neg {
					ex.OffsetS = -d
				}
				t = strings.TrimSpace(s[:i])
				continue
			}
		}
		if i := strings.LastIndexByte(s, '@'); i >= 0 {
			cand := strings.TrimSpace(s[i+1:])
			if cand == "start()" || cand == "end()" {
				ex.AtFunc, ex.HasAt = cand, false
				t = strings.TrimSpace(s[:i])
				continue
			}
			if f, err := strconv.ParseFloat(cand, 64); err == nil {
				ex.At, ex.HasAt, ex.AtFunc = f, true, ""
				t = strings.TrimSpace(s[:i])
				continue
			}
		}
		break
	}

	// 选择器：指标名 + 可选 matcher + 可选范围
	j := 0
	for j < len(t) && isIdentChar(t[j]) {
		j++
	}
	ex.Name = t[:j]
	t = strings.TrimSpace(t[j:])
	if strings.HasPrefix(t, "{") {
		end, err := matchBrace(t, 0, '{', '}')
		if err != nil {
			return nil, err
		}
		ex.Matchers, err = ParseMatchers(t[1:end])
		if err != nil {
			return nil, err
		}
		t = strings.TrimSpace(t[end+1:])
	}
	if strings.HasPrefix(t, "[") {
		end, err := matchBrace(t, 0, '[', ']')
		if err != nil {
			return nil, err
		}
		if strings.Contains(t[:end], ":") {
			return nil, errors.New("子查询必须跟在完整表达式之后")
		}
		d, err := ParseDuration(t[1:end])
		if err != nil {
			return nil, err
		}
		ex.RangeS, ex.HasRange = d, true
		t = strings.TrimSpace(t[end+1:])
	}
	if t != "" {
		return nil, fmt.Errorf("无法解析剩余内容: %q", t)
	}
	if !SelectorLegal(ex.Name, ex.Matchers) {
		return nil, errors.New("向量选择器必须给出指标名或至少一个不匹配空值的 matcher")
	}
	if rangeFuncs[ex.Func] && !ex.HasRange {
		return nil, fmt.Errorf("%s 需要范围向量参数", ex.Func)
	}
	return ex, nil
}
