// Terraform 计划期 unknown 值传播：objchange.ProposedNew 的合并语义。
// 口径来自实际读过的 Terraform v1.9.8 源码 internal/plans/objchange/objchange.go
// 与官方《Types and Values》文档（见 README 参考资料）。
package main

import (
	"fmt"
	"sort"
)

func sortStrings(xs []string) { sort.Strings(xs) }

func fmtv(v interface{}) string { return fmt.Sprintf("%v", v) }

type unknownType struct{}

// UNKNOWN 表示 cty 的 unknown 值（控制台上写作 "known after apply"）。
var UNKNOWN interface{} = unknownType{}

func isUnknown(v interface{}) bool {
	_, ok := v.(unknownType)
	return ok
}

func isNull(v interface{}) bool { return v == nil }

type Attr struct {
	Computed bool
	Optional bool
	Nested   *Nested
}

type Nested struct {
	Nesting string // single | list | map | set
	Schema  map[string]Attr
}

// EmptyValue：所有属性均为 null。
func EmptyValue(schema map[string]Attr) map[string]interface{} {
	out := map[string]interface{}{}
	for name := range schema {
		out[name] = nil
	}
	return out
}

// getAttr 对应 prior.GetAttr(name)；prior 整体 unknown 时按 cty 短路返回 unknown。
func getAttr(value interface{}, name string) interface{} {
	if isUnknown(value) {
		return UNKNOWN
	}
	if isNull(value) {
		return nil
	}
	if m, ok := value.(map[string]interface{}); ok {
		return m[name]
	}
	return nil
}

func optionalValueNotComputable(a Attr, prior interface{}) bool {
	if !a.Optional || a.Nested == nil {
		return false
	}
	if isNull(prior) || isUnknown(prior) {
		return false
	}
	return containsNonComputed(a.Nested.Schema, prior)
}

func containsNonComputed(schema map[string]Attr, value interface{}) bool {
	type pair struct {
		s map[string]Attr
		v interface{}
	}
	stack := []pair{{schema, value}}
	for len(stack) > 0 {
		top := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if isNull(top.v) || isUnknown(top.v) {
			continue
		}
		m, ok := top.v.(map[string]interface{})
		if !ok {
			continue
		}
		for name, spec := range top.s {
			v := m[name]
			if isNull(v) || isUnknown(v) {
				continue
			}
			if spec.Nested != nil {
				stack = append(stack, pair{spec.Nested.Schema, v})
			} else if !spec.Computed {
				return true
			}
		}
	}
	return false
}

// ProposedNew：把 prior 的 computed 值与 config 的托管值合成 proposed new state。
func ProposedNew(schema map[string]Attr, prior, config interface{}) interface{} {
	if isNull(config) && isNull(prior) {
		return prior
	}
	if isNull(prior) {
		prior = EmptyValue(schema)
	}
	if isNull(config) || isUnknown(config) {
		return prior
	}
	out := map[string]interface{}{}
	for name, spec := range schema {
		out[name] = proposedNewAttr(spec, getAttr(prior, name), getAttr(config, name))
	}
	return out
}

func proposedNewAttr(spec Attr, priorV, configV interface{}) interface{} {
	// required 在构造计划时不参与判定：属性实质上只有 computed / 非 computed 两类
	if spec.Computed && isNull(configV) {
		if optionalValueNotComputable(spec, priorV) {
			return configV
		}
		return priorV
	}
	if spec.Nested != nil {
		return proposedNewNested(spec.Nested, priorV, configV)
	}
	return configV
}

func proposedNewNested(ns *Nested, priorV, configV interface{}) interface{} {
	// 整体 unknown 只会出现在 dynamic + unknown for_each 的情况
	if isUnknown(configV) {
		return configV
	}
	switch ns.Nesting {
	case "single":
		if isNull(configV) {
			return configV
		}
		return ProposedNew(ns.Schema, priorV, configV)
	case "list":
		return proposedNewList(ns, priorV, configV)
	case "map":
		return proposedNewMap(ns, priorV, configV)
	default:
		return proposedNewSet(ns, priorV, configV)
	}
}

func toSlice(v interface{}) []interface{} {
	if s, ok := v.([]interface{}); ok {
		return s
	}
	return nil
}

func proposedNewList(ns *Nested, priorV, configV interface{}) interface{} {
	cfg := toSlice(configV)
	if isNull(configV) || isUnknown(configV) || len(cfg) == 0 {
		return configV
	}
	pri := toSlice(priorV)
	out := make([]interface{}, 0, len(cfg))
	for idx, cfgEV := range cfg {
		if isUnknown(priorV) || isNull(priorV) || idx >= len(pri) {
			out = append(out, cfgEV) // 没有对应的 prior 元素 → 原样取 config
			continue
		}
		out = append(out, ProposedNew(ns.Schema, pri[idx], cfgEV))
	}
	return out
}

func proposedNewMap(ns *Nested, priorV, configV interface{}) interface{} {
	cfg, ok := configV.(map[string]interface{})
	if !ok || len(cfg) == 0 {
		return configV
	}
	priorMap := map[string]interface{}{}
	if m, ok2 := priorV.(map[string]interface{}); ok2 {
		priorMap = m
	}
	out := map[string]interface{}{}
	for key, cfgEV := range cfg {
		if _, inPrior := priorMap[key]; !inPrior {
			out[key] = cfgEV
			continue
		}
		out[key] = ProposedNew(ns.Schema, priorMap[key], cfgEV)
	}
	return out
}

// nonComputedSig：set 关联用的签名，只比非 computed 属性。
func nonComputedSig(schema map[string]Attr, value interface{}) string {
	m, ok := value.(map[string]interface{})
	if !ok {
		return ""
	}
	names := make([]string, 0, len(schema))
	for n := range schema {
		names = append(names, n)
	}
	sortStrings(names)
	sig := ""
	for _, n := range names {
		if schema[n].Computed {
			continue
		}
		sig += n + "=" + fmtv(m[n]) + ";"
	}
	return sig
}

func proposedNewSet(ns *Nested, priorV, configV interface{}) interface{} {
	cfg := toSlice(configV)
	if isNull(configV) || isUnknown(configV) || len(cfg) == 0 {
		return configV
	}
	priors := toSlice(priorV)
	used := make([]bool, len(priors))
	out := make([]interface{}, 0, len(cfg))
	for _, cfgEV := range cfg {
		sig := nonComputedSig(ns.Schema, cfgEV)
		hit := -1
		for i, p := range priors {
			if used[i] {
				continue
			}
			if nonComputedSig(ns.Schema, p) == sig {
				hit = i
				break
			}
		}
		if hit < 0 {
			out = append(out, cfgEV)
		} else {
			used[hit] = true
			out = append(out, ProposedNew(ns.Schema, priors[hit], cfgEV))
		}
	}
	return out
}

// PlannedDataResourceObject：用整体 unknown 的 prior 跑同一套逻辑，
// 靠 cty 的 unknown 短路让 unknown 传播进所有「本来要保留 prior」的位置。
func PlannedDataResourceObject(schema map[string]Attr, config interface{}) interface{} {
	return ProposedNew(schema, UNKNOWN, config)
}

// ProviderFillUnknown：provider 的 PlanResourceChange「按需补 unknown」后的结果。
func ProviderFillUnknown(schema map[string]Attr, proposed interface{}) interface{} {
	if isUnknown(proposed) || isNull(proposed) {
		return proposed
	}
	m, ok := proposed.(map[string]interface{})
	if !ok {
		return proposed
	}
	out := map[string]interface{}{}
	for name, spec := range schema {
		v := m[name]
		if spec.Computed && isNull(v) {
			out[name] = UNKNOWN
		} else {
			out[name] = v
		}
	}
	return out
}
