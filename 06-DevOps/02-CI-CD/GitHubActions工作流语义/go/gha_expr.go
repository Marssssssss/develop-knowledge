// Package main —— GitHub Actions 表达式求值器(Go 对照实现, 语义同 python/gha_expr.py)。
//
// 与 Python 版的差异(已在 README 标注): 不含 `*` 对象过滤器与 hashFiles ——
// 前者依赖"可继续投影的中间值"这一动态类型技巧, Go 里需要额外的类型包装, 收益有限;
// 后者的算法官方未公开, Python 版已按"形状"口径实现并标注。
package main

import (
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
)

func isDigit(c byte) bool { return c >= '0' && c <= '9' }
func isHex(c byte) bool {
	return isDigit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')
}
func isAlpha(c byte) bool {
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
}

// ToNumber 官方宽松相等的类型转换表。
func ToNumber(v interface{}) float64 {
	switch t := v.(type) {
	case nil:
		return 0
	case bool:
		if t {
			return 1
		}
		return 0
	case float64:
		return t
	case int:
		return float64(t)
	case string:
		s := strings.TrimSpace(t)
		if s == "" {
			return 0 // 官方: 空串返回 0
		}
		var n float64
		if err := json.Unmarshal([]byte(s), &n); err != nil {
			return math.NaN()
		}
		return n
	}
	return math.NaN() // 数组 / 对象
}

// Truthy 假值集合: false / 0 / -0 / "" / '' / null。
func Truthy(v interface{}) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case float64:
		return t != 0
	case int:
		return t != 0
	case string:
		return t != ""
	}
	return true
}

// ToString 官方"转字符串"表; 数组/对象不转换。
func ToString(v interface{}) string {
	switch t := v.(type) {
	case nil:
		return ""
	case bool:
		if t {
			return "true"
		}
		return "false"
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64)
	case int:
		return strconv.Itoa(t)
	case string:
		return t
	}
	return ""
}

// LooseEq 宽松相等: 字符串忽略大小写; 对象/数组只有同一实例才相等(Go 里保守判 false);
// 其余一律转数字比较。
func LooseEq(a, b interface{}) bool {
	if as, ok := a.(string); ok {
		if bs, ok2 := b.(string); ok2 {
			return strings.EqualFold(as, bs)
		}
	}
	switch a.(type) {
	case []interface{}, map[string]interface{}:
		return false
	}
	switch b.(type) {
	case []interface{}, map[string]interface{}:
		return false
	}
	if ab, ok := a.(bool); ok {
		if bb, ok2 := b.(bool); ok2 {
			return ab == bb
		}
	}
	return ToNumber(a) == ToNumber(b)
}

// Compare 关系运算: 任一操作数为 NaN 时恒 false。
func Compare(op string, a, b interface{}) bool {
	x, y := ToNumber(a), ToNumber(b)
	if math.IsNaN(x) || math.IsNaN(y) {
		return false
	}
	switch op {
	case "<":
		return x < y
	case "<=":
		return x <= y
	case ">":
		return x > y
	case ">=":
		return x >= y
	}
	return false
}

// ---------------------------------------------------------------- 词法

type tok struct{ kind, val string }

func tokenize(src string) ([]tok, error) {
	var out []tok
	i, n := 0, len(src)
	for i < n {
		c := src[i]
		if c == ' ' || c == '\t' || c == '\n' || c == '\r' {
			i++
			continue
		}
		if c == '\'' {
			j := i + 1
			var b strings.Builder
			for {
				if j >= n {
					return nil, fmt.Errorf("字符串未闭合")
				}
				if src[j] == '\'' {
					if j+1 < n && src[j+1] == '\'' {
						b.WriteByte('\'')
						j += 2
						continue
					}
					break
				}
				b.WriteByte(src[j])
				j++
			}
			out = append(out, tok{"str", b.String()})
			i = j + 1
			continue
		}
		if c == '"' {
			return nil, fmt.Errorf("字符串必须用单引号; 双引号会抛错")
		}
		if isDigit(c) || (c == '-' && i+1 < n && (isDigit(src[i+1]) || src[i+1] == '.')) {
			j := i
			if src[j] == '-' {
				j++
			}
			if j+1 < n && src[j] == '0' && (src[j+1] == 'x' || src[j+1] == 'X') {
				j += 2
				for j < n && isHex(src[j]) {
					j++
				}
			} else {
				for j < n && (isDigit(src[j]) || src[j] == '.' || src[j] == 'e' || src[j] == 'E' ||
					((src[j] == '+' || src[j] == '-') && j > i && (src[j-1] == 'e' || src[j-1] == 'E'))) {
					j++
				}
			}
			out = append(out, tok{"num", src[i:j]})
			i = j
			continue
		}
		if isAlpha(c) || c == '_' {
			j := i
			for j < n && (isAlpha(src[j]) || isDigit(src[j]) || src[j] == '_') {
				j++
			}
			w := src[i:j]
			if w == "true" || w == "false" || w == "null" {
				out = append(out, tok{w, w})
			} else {
				out = append(out, tok{"ident", w})
			}
			i = j
			continue
		}
		if i+1 < n {
			two := src[i : i+2]
			switch two {
			case "&&", "||", "==", "!=", "<=", ">=":
				out = append(out, tok{"op", two})
				i += 2
				continue
			}
		}
		if strings.IndexByte("()[]!<>,.*", c) >= 0 {
			out = append(out, tok{"op", string(c)})
			i++
			continue
		}
		return nil, fmt.Errorf("非法字符 %q", string(c))
	}
	return out, nil
}

// ---------------------------------------------------------------- 语法分析

type parser struct {
	toks []tok
	pos  int
	ctx  map[string]interface{}
}

func (p *parser) peek() tok {
	if p.pos < len(p.toks) {
		return p.toks[p.pos]
	}
	return tok{"eof", ""}
}

func (p *parser) eat(kind, val string) (tok, error) {
	t := p.peek()
	if kind != "" && t.kind != kind {
		return t, fmt.Errorf("期望 %s, 得到 %s", kind, t.kind)
	}
	if val != "" && t.val != val {
		return t, fmt.Errorf("期望 %s, 得到 %s", val, t.val)
	}
	p.pos++
	return t, nil
}

func (p *parser) pOr() (interface{}, error) {
	left, err := p.pAnd()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "op" && p.peek().val == "||" {
		p.pos++
		right, err := p.pAnd()
		if err != nil {
			return nil, err
		}
		if !Truthy(left) {
			left = right // 返回操作数本身, 不是布尔值
		}
	}
	return left, nil
}

func (p *parser) pAnd() (interface{}, error) {
	left, err := p.pCmp()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "op" && p.peek().val == "&&" {
		p.pos++
		right, err := p.pCmp()
		if err != nil {
			return nil, err
		}
		if Truthy(left) {
			left = right
		}
	}
	return left, nil
}

func (p *parser) pCmp() (interface{}, error) {
	left, err := p.pRel()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "op" && (p.peek().val == "==" || p.peek().val == "!=") {
		op := p.peek().val
		p.pos++
		right, err := p.pRel()
		if err != nil {
			return nil, err
		}
		eq := LooseEq(left, right)
		left = eq
		if op == "!=" {
			left = !eq
		}
	}
	return left, nil
}

func (p *parser) pRel() (interface{}, error) {
	left, err := p.pUnary()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "op" {
		op := p.peek().val
		if op != "<" && op != "<=" && op != ">" && op != ">=" {
			break
		}
		p.pos++
		right, err := p.pUnary()
		if err != nil {
			return nil, err
		}
		left = Compare(op, left, right)
	}
	return left, nil
}

func (p *parser) pUnary() (interface{}, error) {
	if p.peek().kind == "op" && p.peek().val == "!" {
		p.pos++
		v, err := p.pUnary()
		if err != nil {
			return nil, err
		}
		return !Truthy(v), nil
	}
	return p.pPostfix()
}

func (p *parser) pPostfix() (interface{}, error) {
	v, err := p.pPrimary()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "op" && (p.peek().val == "." || p.peek().val == "[") {
		if p.peek().val == "." {
			p.pos++
			name, err := p.eat("ident", "")
			if err != nil {
				return nil, err
			}
			v = Deref(v, name.val)
		} else {
			p.pos++
			idx, err := p.pOr()
			if err != nil {
				return nil, err
			}
			if _, err := p.eat("op", "]"); err != nil {
				return nil, err
			}
			v = Index(v, idx)
		}
	}
	return v, nil
}

func (p *parser) pPrimary() (interface{}, error) {
	t := p.peek()
	switch t.kind {
	case "str":
		p.pos++
		return t.val, nil
	case "num":
		p.pos++
		return parseNumber(t.val)
	case "true":
		p.pos++
		return true, nil
	case "false":
		p.pos++
		return false, nil
	case "null":
		p.pos++
		return nil, nil
	case "op":
		if t.val == "(" {
			p.pos++
			v, err := p.pOr()
			if err != nil {
				return nil, err
			}
			if _, err := p.eat("op", ")"); err != nil {
				return nil, err
			}
			return v, nil
		}
	case "ident":
		p.pos++
		if p.peek().kind == "op" && p.peek().val == "(" {
			p.pos++
			var args []interface{}
			if !(p.peek().kind == "op" && p.peek().val == ")") {
				a, err := p.pOr()
				if err != nil {
					return nil, err
				}
				args = append(args, a)
				for p.peek().kind == "op" && p.peek().val == "," {
					p.pos++
					a, err := p.pOr()
					if err != nil {
						return nil, err
					}
					args = append(args, a)
				}
			}
			if _, err := p.eat("op", ")"); err != nil {
				return nil, err
			}
			return callFunc(t.val, args, p.ctx)
		}
		return p.ctx[t.val], nil
	}
	return nil, fmt.Errorf("意外的 token %s %q", t.kind, t.val)
}

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
