// 本文件实现记录级变换与过滤:attributes processor 的六种动作、filter 条件
// 求值。条件语言是「路径 运算符 字面量」的极简 OTTL 子集。
//
// 注意 Go 的 regexp.MatchString 是搜索语义,与 Python 的 re.fullmatch 不同,
// 这里显式加锚点对齐,否则 "/readyz" 会被 "/ready" 命中。

package main

import (
	"crypto/sha1"
	"encoding/hex"
	"regexp"
	"strings"
)

// ---------------------------------------------------------------- attributes

// Action attributes processor 的单个动作。
type Action struct {
	Act   string
	Key   string
	Value string
	From  string
}

// AttributesProcessor insert/update/upsert/delete/hash/extract 六种动作。
type AttributesProcessor struct{ Actions []Action }

var knownActions = []string{"insert", "update", "upsert", "delete", "hash", "extract"}

// NewAttributesProcessor 校验动作名。
func NewAttributesProcessor(actions []Action) (*AttributesProcessor, error) {
	for _, a := range actions {
		if !has(knownActions, a.Act) {
			return nil, cfgErr("unknown attributes action: %q", a.Act)
		}
	}
	return &AttributesProcessor{Actions: actions}, nil
}

// HashValue 用 SHA-1 替换原值(官方口径),保留关联性而不落原始值。
func HashValue(v string) string {
	sum := sha1.Sum([]byte(v))
	return hex.EncodeToString(sum[:])
}

// Apply 依次施加动作并返回记录本身。
func (a *AttributesProcessor) Apply(rec Record) Record {
	for _, act := range a.Actions {
		switch act.Act {
		case "insert":
			if _, ok := rec.Attrs[act.Key]; !ok {
				rec.Attrs[act.Key] = act.Value
			}
		case "update":
			if _, ok := rec.Attrs[act.Key]; ok {
				rec.Attrs[act.Key] = act.Value
			}
		case "upsert":
			rec.Attrs[act.Key] = act.Value
		case "delete":
			delete(rec.Attrs, act.Key)
		case "hash":
			if v, ok := rec.Attrs[act.Key]; ok {
				rec.Attrs[act.Key] = HashValue(v)
			}
		case "extract":
			if v, ok := rec.Attrs[act.From]; ok {
				rec.Attrs[act.Key] = v
			}
		}
	}
	return rec
}

// ---------------------------------------------------------------- filter

var condRe = regexp.MustCompile(`^([a-zA-Z0-9_.]+)\s*(==|!=|=~|!~|<=|>=|<|>)\s*(.+)$`)

// EvalCondition 求值单条条件;语法非法返回错误。
// 属性缺失时比较类运算符一律不命中 —— 因此 `!=` 对缺失属性会返回 true。
func EvalCondition(cond string, rec Record) (bool, error) {
	m := condRe.FindStringSubmatch(strings.TrimSpace(cond))
	if m == nil {
		return false, cfgErr("无法解析的过滤条件: %s", cond)
	}
	key, op, rhs := m[1], m[2], strings.TrimSpace(m[3])
	want := strings.Trim(strings.Trim(rhs, `"`), `'`)
	val, hasVal := rec.Attrs[key]
	switch op {
	case "=~", "!~":
		// Go 的 MatchString 是「搜索」语义,Python 端的 fullmatch 是整串匹配;
		// 这里显式加锚点对齐,否则 "/readyz" 会被 "/ready" 命中。
		re, err := regexp.Compile("^(?:" + want + ")$")
		if err != nil {
			return false, cfgErr("非法正则: %s", rhs)
		}
		hit := re.MatchString(val)
		if op == "=~" {
			return hit, nil
		}
		return !hit, nil
	case "==", "!=":
		hit := hasVal && val == want
		if op == "==" {
			return hit, nil
		}
		return !hit, nil
	default:
		if !hasVal {
			return false, nil
		}
		v, ok1 := parseFloat(val)
		n, ok2 := parseFloat(want)
		if !ok1 || !ok2 {
			return false, nil
		}
		switch op {
		case "<":
			return v < n, nil
		case ">":
			return v > n, nil
		case "<=":
			return v <= n, nil
		case ">=":
			return v >= n, nil
		}
	}
	return false, nil
}

// FilterProcessor 任一条件命中即丢弃记录(OR 语义)。
// 注意正则按整串匹配(等价 OTTL 的 matches),前缀不算命中。
type FilterProcessor struct {
	Conditions []string
	ErrorMode  string
}

// NewFilterProcessor 校验 error_mode。
func NewFilterProcessor(conds []string, errorMode string) (*FilterProcessor, error) {
	if errorMode != "ignore" && errorMode != "propagate" {
		return nil, cfgErr("error_mode 只能取 ignore / propagate")
	}
	return &FilterProcessor{Conditions: conds, ErrorMode: errorMode}, nil
}

// Matches 判定该记录是否应被丢弃。
func (f *FilterProcessor) Matches(rec Record) (bool, error) {
	for _, c := range f.Conditions {
		hit, err := EvalCondition(c, rec)
		if err != nil {
			if f.ErrorMode == "propagate" {
				return false, err
			}
			continue
		}
		if hit {
			return true, nil
		}
	}
	return false, nil
}

// WastedWork limiter 拒绝整批时,它前面那些组件白做的工作量(以操作次数计)。
func WastedWork(after []interface{}, n int) int {
	cost := 0
	for _, p := range after {
		switch v := p.(type) {
		case *FilterProcessor:
			cost += n * 2 * len(v.Conditions)
		case *AttributesProcessor:
			cost += n * len(v.Actions)
		}
	}
	return cost
}

// FanoutLatencyMs 同一次扇出耗时:串行求和,并行取最慢分支。
func FanoutLatencyMs(branch []int, parallel bool) int {
	if len(branch) == 0 {
		return 0
	}
	if parallel {
		max := branch[0]
		for _, v := range branch[1:] {
			if v > max {
				max = v
			}
		}
		return max
	}
	sum := 0
	for _, v := range branch {
		sum += v
	}
	return sum
}
