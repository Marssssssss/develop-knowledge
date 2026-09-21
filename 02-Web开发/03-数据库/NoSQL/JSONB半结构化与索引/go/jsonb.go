// Package jsonb —— PostgreSQL jsonb 与 GIN 索引（官方文档转写）。
//
// 与 Python 版的**显式语言差异**：
//   * Python 的动态 dict/list 改成 Go 的 map[string]any / []any，
//     JSON 的 null 是 nil，而 Go 里「键不存在」与「键存在但为 nil」必须分开判断；
//   * Python 的 Numeric 用 float 承载，这里同样用 float64，
//     但 **float64 没有 NaN 可比性**，`v != v` 才是 NaN 的正确判据；
//   * Python 抛异常，这里统一用 (value, error)。
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
)

var (
	ErrNullByte = errors.New(`unsupported Unicode escape sequence: \u0000`)
	ErrSurrogate = errors.New("Unicode surrogate pairs must be well-formed in jsonb")
	ErrOutOfRange = errors.New("number is out of range for type numeric")
	ErrOpclass = errors.New("unknown opclass")
)

// Num 是 PG numeric 的极简模型：按值比较，1 与 1.0 相等。
type Num struct{ V float64 }

func (n Num) String() string { return fmt.Sprint(n.V) }

func numEq(a, b any) bool {
	an, aok := a.(Num)
	bn, bok := b.(Num)
	switch {
	case aok && bok:
		return an.V == bn.V
	case aok:
		f, ok := toFloat(b)
		return ok && an.V == f
	case bok:
		f, ok := toFloat(a)
		return ok && f == bn.V
	}
	af, aok := toFloat(a)
	bf, bok := toFloat(b)
	if aok && bok {
		return af == bf
	}
	if s, ok := a.(string); ok {
		if t, ok2 := b.(string); ok2 {
			return s == t
		}
	}
	return false
}

func toFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case int:
		return float64(x), true
	case Num:
		return x.V, true
	}
	return 0, false
}

// normalize 把解码后的 any 规范化成 jsonb：对象键排序、数字成 Num。
func normalize(v any) any {
	switch x := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, val := range x {
			out[k] = normalize(val)
		}
		return out // Go 的 map 本身无序，取用时统一排序即可
	case []any:
		out := make([]any, len(x))
		for i, val := range x {
			out[i] = normalize(val)
		}
		return out
	case float64:
		return Num{x}
	}
	return v
}

// ParseJSONB 是 jsonb 输入函数：对**原始文本**做两条更严格的检查。
func ParseJSONB(text string) (any, error) {
	if strings.Contains(text, `\u0000`) {
		return nil, ErrNullByte
	}
	if hasLoneSurrogate(text) {
		return nil, ErrSurrogate
	}
	var raw any
	if err := json.Unmarshal([]byte(text), &raw); err != nil {
		return nil, err
	}
	v := normalize(raw)
	if err := checkRange(v); err != nil {
		return nil, err
	}
	return v, nil
}

func checkRange(v any) error {
	switch x := v.(type) {
	case Num:
		if math.IsInf(x.V, 0) || math.IsNaN(x.V) {
			return ErrOutOfRange
		}
	case map[string]any:
		for _, val := range x {
			if err := checkRange(val); err != nil {
				return err
			}
		}
	case []any:
		for _, val := range x {
			if err := checkRange(val); err != nil {
				return err
			}
		}
	}
	return nil
}

func hasLoneSurrogate(s string) bool {
	for i := 0; i < len(s)-5; i++ {
		if s[i] != '\\' || s[i+1] != 'u' {
			continue
		}
		hexPart := s[i+2 : i+6]
		cp, err := parseHex4(hexPart)
		if err != nil {
			continue
		}
		if cp >= 0xD800 && cp <= 0xDBFF {
			if i+12 > len(s) || s[i+6] != '\\' || s[i+7] != 'u' {
				return true
			}
			lo, err := parseHex4(s[i+8 : i+12])
			if err != nil || lo < 0xDC00 || lo > 0xDFFF {
				return true
			}
			i += 11
			continue
		}
		if cp >= 0xDC00 && cp <= 0xDFFF {
			return true
		}
	}
	return false
}

func parseHex4(s string) (int64, error) {
	if len(s) != 4 {
		return 0, errors.New("bad hex")
	}
	var v int64
	for _, c := range s {
		var d int64
		switch {
		case c >= '0' && c <= '9':
			d = int64(c - '0')
		case c >= 'a' && c <= 'f':
			d = int64(c-'a') + 10
		case c >= 'A' && c <= 'F':
			d = int64(c-'A') + 10
		default:
			return 0, errors.New("bad hex")
		}
		v = v*16 + d
	}
	return v, nil
}

// Contains 是 @>：结构与数据内容都要匹配，允许丢弃包含者里多余的元素/键值。
func Contains(doc, needle any) bool {
	switch n := needle.(type) {
	case map[string]any:
		d, ok := doc.(map[string]any)
		if !ok {
			return false
		}
		for k, v := range n {
			dv, ok := d[k]
			if !ok {
				return false
			}
			if !Contains(dv, v) {
				return false
			}
		}
		return true
	case []any:
		d, ok := doc.([]any)
		if !ok {
			return false // 官方：数组包含顶层标量是唯一的例外，且不反向
		}
		for _, e := range n {
			if !arrayContainsOne(d, e) {
				return false
			}
		}
		return true
	}
	if d, ok := doc.([]any); ok {
		return arrayContainsOne(d, needle)
	}
	if _, ok := doc.(map[string]any); ok {
		return false
	}
	return numEq(doc, needle)
}

func arrayContainsOne(arr []any, needle any) bool {
	for _, e := range arr {
		if numEq(e, needle) || deepEq(e, needle) {
			return true
		}
	}
	return false
}

func deepEq(a, b any) bool {
	switch x := a.(type) {
	case map[string]any:
		y, ok := b.(map[string]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for k, v := range x {
			yv, ok := y[k]
			if !ok || !deepEq(v, yv) {
				return false
			}
		}
		return true
	case []any:
		y, ok := b.([]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for i := range x {
			if !deepEq(x[i], y[i]) {
				return false
			}
		}
		return true
	}
	return numEq(a, b)
}

// ExistsTop 是 ? ：字符串必须是**顶层**对象键或顶层数组元素。
func ExistsTop(doc any, key string) bool {
	switch d := doc.(type) {
	case map[string]any:
		_, ok := d[key]
		return ok
	case []any:
		for _, e := range d {
			if s, ok := e.(string); ok && s == key {
				return true
			}
		}
	}
	return false
}
