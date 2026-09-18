// Package main 以纯手写实现建模 OpenTelemetry Collector 的配置装配、
// processor 链与 exporter helper 语义,并实跑断言自检。
//
// 覆盖:type[/name] 复合键、六段组件表、pipeline 引用校验、receiver/exporter
// 两侧 fanout、connector 依赖图与环检测。资料来源见 ../README.md「参考资料」。
package main

import (
	"fmt"
	"sort"
)

// Pipeline 一条 pipeline:按信号类型分,connectors 可出现在两端。
type Pipeline struct {
	Signal     string
	Name       string
	Receivers  []string
	Processors []string
	Exporters  []string
}

// Key 形如 `traces` 或 `traces/primary`。
func (p Pipeline) Key() string {
	if p.Name == "" {
		return p.Signal
	}
	return p.Signal + "/" + p.Name
}

// Config 六段组件表 + pipelines + service.extensions。
type Config struct {
	Sections   map[string][]string
	Pipelines  []Pipeline
	ServiceExt []string
}

// NewConfig 建一张空配置表并补齐六段。
func NewConfig() *Config {
	c := &Config{Sections: map[string][]string{}}
	for _, s := range []string{"receivers", "processors", "exporters", "connectors", "extensions"} {
		c.Sections[s] = nil
	}
	return c
}

func (c *Config) sec(section string) []string { return c.Sections[section] }

// Pipeline 按 key 查找。
func (c *Config) Pipeline(key string) (Pipeline, bool) {
	for _, p := range c.Pipelines {
		if p.Key() == key {
			return p, true
		}
	}
	return Pipeline{}, false
}

// Validate 校验引用完整性;非致命问题(未使用组件)以告警切片返回。
func (c *Config) Validate() ([]string, error) {
	if len(c.Pipelines) == 0 {
		return nil, cfgErr("service.pipelines 不能为空")
	}
	seen := map[string]bool{}
	for _, p := range c.Pipelines {
		if seen[p.Key()] {
			return nil, cfgErr("重复的 pipeline: %s", p.Key())
		}
		seen[p.Key()] = true
	}
	for _, ids := range c.Sections {
		for _, cid := range ids {
			if _, _, err := ParseComponentID(cid); err != nil {
				return nil, err
			}
		}
	}
	for _, p := range c.Pipelines {
		for _, cid := range p.Receivers {
			if !has(c.sec("receivers"), cid) && !has(c.sec("connectors"), cid) {
				return nil, cfgErr("pipeline %s 引用了未定义的 receiver: %s", p.Key(), cid)
			}
		}
		for _, cid := range p.Processors {
			if !has(c.sec("processors"), cid) {
				return nil, cfgErr("pipeline %s 引用了未定义的 processor: %s", p.Key(), cid)
			}
		}
		for _, cid := range p.Exporters {
			if !has(c.sec("exporters"), cid) && !has(c.sec("connectors"), cid) {
				return nil, cfgErr("pipeline %s 引用了未定义的 exporter: %s", p.Key(), cid)
			}
		}
	}
	for _, cid := range c.ServiceExt {
		if !has(c.sec("extensions"), cid) {
			return nil, cfgErr("service.extensions 引用了未定义的 extension: %s", cid)
		}
	}
	return c.warnings(), nil
}

// warnings 列出未被任何 pipeline 引用的组件:真实 Collector 只打日志。
func (c *Config) warnings() []string {
	usedR, usedP, usedE := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, p := range c.Pipelines {
		for _, x := range p.Receivers {
			usedR[x] = true
		}
		for _, x := range p.Processors {
			usedP[x] = true
		}
		for _, x := range p.Exporters {
			usedE[x] = true
		}
	}
	var out []string
	appendUnused := func(section, prefix string, used map[string]bool) {
		var names []string
		for _, cid := range c.sec(section) {
			if !used[cid] {
				names = append(names, cid)
			}
		}
		sort.Strings(names)
		for _, n := range names {
			out = append(out, prefix+": "+n)
		}
	}
	appendUnused("receivers", "unused receiver", usedR)
	appendUnused("processors", "unused processor", usedP)
	appendUnused("exporters", "unused exporter", usedE)
	conns := append([]string(nil), c.sec("connectors")...)
	sort.Strings(conns)
	for _, cid := range conns {
		if !usedR[cid] || !usedE[cid] {
			out = append(out, "connector 未同时出现在 receiver/exporter 位: "+cid)
		}
	}
	return out
}

// ReceiverFanout receiver -> 引用它的 pipeline(定义序)。同一份入站数据被
// 扇出到多路。
func (c *Config) ReceiverFanout() map[string][]string {
	out := map[string][]string{}
	for _, p := range c.Pipelines {
		for _, r := range p.Receivers {
			out[r] = append(out[r], p.Key())
		}
	}
	return out
}

// ExporterFanout pipeline -> 它广播到的 exporter 列表。
func (c *Config) ExporterFanout() map[string][]string {
	out := map[string][]string{}
	for _, p := range c.Pipelines {
		out[p.Key()] = append([]string(nil), p.Exporters...)
	}
	return out
}

// ConnectorEdges connector c:把 c 当 exporter 的 pipeline -> 把 c 当 receiver
// 的 pipeline。
func (c *Config) ConnectorEdges() [][2]string {
	asExpr, asRecv := map[string][]string{}, map[string][]string{}
	for _, p := range c.Pipelines {
		for _, e := range p.Exporters {
			if has(c.sec("connectors"), e) {
				asExpr[e] = append(asExpr[e], p.Key())
			}
		}
		for _, r := range p.Receivers {
			if has(c.sec("connectors"), r) {
				asRecv[r] = append(asRecv[r], p.Key())
			}
		}
	}
	var edges [][2]string
	var conns []string
	for cid := range asExpr {
		conns = append(conns, cid)
	}
	for cid := range asRecv {
		if !has(conns, cid) {
			conns = append(conns, cid)
		}
	}
	sort.Strings(conns)
	for _, cid := range conns {
		for _, a := range asExpr[cid] {
			for _, b := range asRecv[cid] {
				edges = append(edges, [2]string{a, b})
			}
		}
	}
	return edges
}

// TopoOrder connector 依赖图的拓扑序;有环返回错误。
func (c *Config) TopoOrder() ([]string, error) {
	var nodes []string
	indeg := map[string]int{}
	adj := map[string][]string{}
	for _, p := range c.Pipelines {
		nodes = append(nodes, p.Key())
		indeg[p.Key()] = 0
	}
	sort.Strings(nodes)
	for _, e := range c.ConnectorEdges() {
		adj[e[0]] = append(adj[e[0]], e[1])
		indeg[e[1]]++
	}
	var queue, order []string
	for _, n := range nodes {
		if indeg[n] == 0 {
			queue = append(queue, n)
		}
	}
	for len(queue) > 0 {
		n := queue[0]
		queue = queue[1:]
		order = append(order, n)
		for _, m := range adj[n] {
			indeg[m]--
			if indeg[m] == 0 {
				queue = append(queue, m)
			}
		}
	}
	if len(order) != len(nodes) {
		var cyc []string
		for _, n := range nodes {
			if !has(order, n) {
				cyc = append(cyc, n)
			}
		}
		return nil, cfgErr("pipeline 依赖存在环,涉及: %v", cyc)
	}
	return order, nil
}

// MemoryLimiterFirst 判断 memory_limiter 是否位于该 pipeline 首位。
func (c *Config) MemoryLimiterFirst(key string) bool {
	p, ok := c.Pipeline(key)
	if !ok || len(p.Processors) == 0 {
		return false
	}
	return ComponentType(p.Processors[0]) == "memory_limiter"
}

