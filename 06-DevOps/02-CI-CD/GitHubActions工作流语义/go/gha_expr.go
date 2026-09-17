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

