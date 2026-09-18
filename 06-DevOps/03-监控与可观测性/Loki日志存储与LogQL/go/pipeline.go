package main

// 管线阶段与执行器。
//
// 核心论点与 Python 版一致：**管线错误不丢行，只写 __error__ 标签**。
// 于是「想丢掉错误行」必须显式加一个过 `__error__` 的过滤阶段；
// 而**指标查询不允许携带错误**，只要范围聚合前仍残留错误标签，整条查询失败。
//
// 这里用 Stage 接口 + 具名结构体表达阶段，而不是从查询文本解析 ——
// Go 版不实现 LogQL 词法/语法分析（README 已说明），阶段由调用方直接构造。

import (
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
)

// Stage 处理一条日志；返回 false 表示该行被过滤掉。
type Stage interface {
	Apply(e *Entry) bool
}

// ---------------------------------------------------------------- 行过滤

type LineFilter struct {
	Op    string
	Value string
}

func (f LineFilter) Apply(e *Entry) bool {
	switch f.Op {
	case "|=":
		return strings.Contains(e.Line, f.Value)
	case "!=":
		return !strings.Contains(e.Line, f.Value)
	}
	// |~ / !~ 是**搜索**语义（非锚定）：|~ "err" 命中任何含 err 的行。
	// 这与流选择器 =~ 的完全锚定刻意相反，是 LogQL 最常写错的一处。
	hit := regexp.MustCompile(f.Value).MatchString(e.Line)
	if f.Op == "|~" {
		return hit
	}
	return !hit
}

// ---------------------------------------------------------------- JSON 解析

type JSONParser struct{}

func (JSONParser) Apply(e *Entry) bool {
	var obj map[string]interface{}
	if err := json.Unmarshal([]byte(e.Line), &obj); err != nil {
		// 解析失败不丢行，只打标签 —— 官方原文如此。
		e.AppendError(JSONParserErr)
		return true
	}
	flattenInto(e.Labels, obj, "")
	return true
}

// flattenInto 把嵌套 JSON 摊平，层级之间用 _ 连接（Loki json 解析器默认行为）。
func flattenInto(dst map[string]string, obj map[string]interface{}, prefix string) {
	for k, v := range obj {
		name := k
		if prefix != "" {
			name = prefix + "_" + k
		}
		if nested, ok := v.(map[string]interface{}); ok {
			flattenInto(dst, nested, name)
			continue
		}
		dst[name] = stringifyJSON(v)
	}
}

func stringifyJSON(v interface{}) string {
	switch t := v.(type) {
	case string:
		return t
	case bool:
		if t {
			return "true"
		}
		return "false"
	case nil:
		return "null"
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64)
	}
	buf, err := json.Marshal(v)
	if err != nil {
		return ""
	}
	return string(buf)
}

// ---------------------------------------------------------------- 标签比较

// LabelCompare 单个标签比较。Numeric 为 true 时走数值口径。
type LabelCompare struct {
	Label   string
	Op      string
	Value   string
	Numeric bool
}

func (c LabelCompare) Apply(e *Entry) bool {
	raw := e.Labels[c.Label]
	if !c.Numeric {
		if c.Op == "=" {
			return raw == c.Value
		}
		return raw != c.Value
	}
	got, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		// 数值比较拿不到数字 —— 官方口径是「不丢行、打错误标签」，
		// 所以这里返回 true 把行放过去，而不是把它过滤掉。
		e.AppendError(SampleExtractionErr)
		return true
	}
	want, err := strconv.ParseFloat(c.Value, 64)
	if err != nil {
		e.AppendError(SampleExtractionErr)
		return true
	}
	switch c.Op {
	case "=":
		return got == want
	case "!=":
		return got != want
	case ">":
		return got > want
	case ">=":
		return got >= want
	case "<":
		return got < want
	}
	return got <= want
}

// ErrorFilter 显式过滤管线错误行。
// KeepErrors=false 对应 `| __error__ = ""`，true 对应 `| __error__ != ""`。
type ErrorFilter struct {
	KeepErrors bool
}

func (f ErrorFilter) Apply(e *Entry) bool {
	_, has := e.Labels[ErrorLabel]
	return has == f.KeepErrors
}

// ---------------------------------------------------------------- unwrap

type UnwrapStage struct {
	Field string
}

func (u UnwrapStage) Apply(e *Entry) bool {
	raw, ok := e.Labels[u.Field]
	if !ok {
		e.AppendError(SampleExtractionErr)
		return true
	}
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		e.AppendError(SampleExtractionErr)
		return true
	}
	e.Value = value
	e.HasValue = true
	// **成功 unwrap 后必须把该标签从标签集里删掉。**
	// 解析器产出的标签会成为指标序列标识的一部分；若 unwrap 出来的标签继续
	// 留在标识里，每个不同取值都会裂出一条独立序列、每条只有一个样本，
	// avg / stddev / quantile 全部退化成恒等运算。
	delete(e.Labels, u.Field)
	return true
}

// RunPipeline 按顺序跑完管线；返回 nil 表示该行被过滤掉。
func RunPipeline(entry *Entry, stages []Stage) *Entry {
	for _, s := range stages {
		if !s.Apply(entry) {
			return nil
		}
	}
	return entry
}
