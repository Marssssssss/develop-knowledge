// Package main —— 表达式求值器的函数库与 Eval 入口(与 gha_expr.go / gha_eval.go 同包)。
// 拆自原 593 行的单文件, 以满足 OPTIMIZATION.md §1.1 的"单源文件 ≤ 300 行"。
package main

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

func parseNumber(s string) (interface{}, error) {
	neg := strings.HasPrefix(s, "-")
	body := strings.TrimPrefix(s, "-")
	if strings.HasPrefix(body, "0x") || strings.HasPrefix(body, "0X") {
		n, err := strconv.ParseInt(body[2:], 16, 64)
		if err != nil {
			return nil, err
		}
		if neg {
			n = -n
		}
		return float64(n), nil
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return nil, err
	}
	return f, nil
}

func Deref(v interface{}, name string) interface{} {
	if m, ok := v.(map[string]interface{}); ok {
		return m[name]
	}
	return nil
}

func Index(v, idx interface{}) interface{} {
	if m, ok := v.(map[string]interface{}); ok {
		if s, ok2 := idx.(string); ok2 {
			return m[s]
		}
	}
	if arr, ok := v.([]interface{}); ok {
		if n, ok2 := idx.(float64); ok2 {
			k := int(n)
			if k >= 0 && k < len(arr) {
				return arr[k]
			}
			return nil
		}
		for _, x := range arr {
			if LooseEq(x, idx) {
				return x
			}
		}
	}
	return nil
}

func callFunc(name string, args []interface{}, ctx map[string]interface{}) (interface{}, error) {
	arg := func(i int) interface{} {
		if i < len(args) {
			return args[i]
		}
		return nil
	}
	switch name {
	case "contains":
		if arr, ok := arg(0).([]interface{}); ok {
			for _, x := range arr {
				if LooseEq(x, arg(1)) {
					return true, nil
				}
			}
			return false, nil
		}
		return strings.Contains(strings.ToLower(ToString(arg(0))), strings.ToLower(ToString(arg(1)))), nil
	case "startsWith":
		return strings.HasPrefix(strings.ToLower(ToString(arg(0))), strings.ToLower(ToString(arg(1)))), nil
	case "endsWith":
		return strings.HasSuffix(strings.ToLower(ToString(arg(0))), strings.ToLower(ToString(arg(1)))), nil
	case "format":
		return formatString(ToString(arg(0)), args[1:]), nil
	case "join":
		sep := ","
		if len(args) > 1 {
			sep = ToString(args[1])
		}
		var parts []string
		if arr, ok := arg(0).([]interface{}); ok {
			for _, x := range arr {
				parts = append(parts, ToString(x))
			}
		} else {
			parts = append(parts, ToString(arg(0)))
		}
		return strings.Join(parts, sep), nil
	case "toJSON":
		b, err := json.Marshal(arg(0))
		if err != nil {
			return nil, err
		}
		return string(b), nil
	case "fromJSON":
		var out interface{}
		if err := json.Unmarshal([]byte(ToString(arg(0))), &out); err != nil {
			return nil, nil
		}
		return out, nil
	case "success", "failure", "cancelled", "always":
		st, _ := ctx["__status__"].(map[string]interface{})
		failed, _ := st["any_failed"].(bool)
		cancelled, _ := st["cancelled"].(bool)
		switch name {
		case "success":
			return !failed, nil
		case "failure":
			return failed, nil
		case "cancelled":
			return cancelled, nil
		}
		return true, nil
	}
	return nil, fmt.Errorf("未知函数 %s", name)
}

func formatString(tpl string, vals []interface{}) string {
	var b strings.Builder
	for i := 0; i < len(tpl); {
		c := tpl[i]
		if c == '{' && i+1 < len(tpl) && tpl[i+1] == '{' {
			b.WriteByte('{')
			i += 2
			continue
		}
		if c == '}' && i+1 < len(tpl) && tpl[i+1] == '}' {
			b.WriteByte('}')
			i += 2
			continue
		}
		if c == '{' {
			j := strings.IndexByte(tpl[i:], '}')
			if j < 1 {
				b.WriteByte(c)
				i++
				continue
			}
			k, err := strconv.Atoi(tpl[i+1 : i+j])
			if err == nil && k >= 0 && k < len(vals) {
				b.WriteString(ToString(vals[k]))
			}
			i += j + 1
			continue
		}
		b.WriteByte(c)
		i++
	}
	return b.String()
}

// Eval 求值一段表达式(不含 ${{ }} 包裹)。
func Eval(src string, ctx map[string]interface{}) (interface{}, error) {
	toks, err := tokenize(src)
	if err != nil {
		return nil, err
	}
	p := &parser{toks: toks, ctx: ctx}
	v, err := p.pOr()
	if err != nil {
		return nil, err
	}
	if p.pos != len(p.toks) {
		return nil, fmt.Errorf("表达式末尾有多余 token")
	}
	return v, nil
}
