// 本文件是包内共享的基础类型:记录抽象、配置错误、组件 ID 解析与数值工具。
// 语义与默认值的出处见 ../README.md「参考资料」。

package main

import (
	"fmt"
	"regexp"
	"strconv"
)

// Record 是一条 telemetry 记录(trace span / metric point / log record 的统一
// 抽象)。属性统一以字符串保存,数值比较时再解析 —— 便于跨语言对齐断言。
type Record struct {
	Attrs map[string]string
}

func newRecord(kv ...string) Record {
	m := map[string]string{}
	for i := 0; i+1 < len(kv); i += 2 {
		m[kv[i]] = kv[i+1]
	}
	return Record{Attrs: m}
}

func (r Record) Copy() Record {
	m := make(map[string]string, len(r.Attrs))
	for k, v := range r.Attrs {
		m[k] = v
	}
	return Record{Attrs: m}
}

// ConfigError 表示配置非法;真实 Collector 在启动 service 之前就拒绝加载。
type ConfigError struct{ Msg string }

func (e *ConfigError) Error() string { return e.Msg }

func cfgErr(format string, a ...interface{}) error {
	return &ConfigError{Msg: fmt.Sprintf(format, a...)}
}

// ID 的 type 与 name 共用同一条正则:两段都必须以字母开头。
// 因此 `traces/2` 这类 pipeline 名会被拒 —— 容易踩的坑。
var idRe = regexp.MustCompile(`^[a-zA-Z][0-9a-zA-Z_]*(?:/[a-zA-Z][0-9a-zA-Z_]*)?$`)

// ParseComponentID 把 `batch/traces` 拆成 ("batch", "traces"),`otlp` 拆成
// ("otlp", "")。
func ParseComponentID(cid string) (string, string, error) {
	if !idRe.MatchString(cid) {
		return "", "", cfgErr("非法组件 ID: %q", cid)
	}
	for i := 0; i < len(cid); i++ {
		if cid[i] == '/' {
			return cid[:i], cid[i+1:], nil
		}
	}
	return cid, "", nil
}

// ComponentType 取 type 部分;ID 非法时返回空串。
func ComponentType(cid string) string {
	t, _, err := ParseComponentID(cid)
	if err != nil {
		return ""
	}
	return t
}

// IsSameType 判断两个 ID 是否同 type 不同 name(同一组件的两个实例)。
func IsSameType(a, b string) bool {
	ta, _, ea := ParseComponentID(a)
	tb, _, eb := ParseComponentID(b)
	if ea != nil || eb != nil {
		return false
	}
	return ta == tb
}

func has(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// parseFloat 宽松解析,失败返回 (0,false)。
func parseFloat(s string) (float64, bool) {
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0, false
	}
	return v, true
}
