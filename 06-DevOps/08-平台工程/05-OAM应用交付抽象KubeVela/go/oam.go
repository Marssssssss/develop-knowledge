// Package main 转写 oam.py：KubeVela OAM 应用模型与定义修订（Go 侧）。
package main

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"strings"
)

// OamError 表示模型约束被破坏。
var OamError = errors.New("oam error")

// DefinitionTypes 是 DefinitionType 的五个枚举值。
var DefinitionTypes = []string{"Component", "Trait", "Policy", "WorkflowStep", "Source"}

// TerraformTypes 与默认类型（源码 kubebuilder 标注：默认 hcl）。
var TerraformTypes = []string{"hcl", "json", "remote"}

const defaultTerraformType = "hcl"

// ParameterValueType 按三值归类；bool 必须排在数值之前判断（Go 里 bool 不是 int）。
func ParameterValueType(v any) (string, error) {
	switch v.(type) {
	case bool:
		return "boolean", nil
	case int, int64, float64:
		return "number", nil
	case string:
		return "string", nil
	default:
		return "", fmt.Errorf("%w: unsupported parameter value %v", OamError, v)
	}
}

// Trait 对应 common.ApplicationTrait。
type Trait struct {
	Type       string
	Properties map[string]any
}

// Component 对应 common.ApplicationComponent。Traits 是**数组**，顺序敏感。
type Component struct {
	Name        string
	Type        string
	Properties  map[string]any
	Traits      []Trait
	DependsOn   []string
	Scopes      map[string]string
	ReplicaKey  string // json:"-"，不出现在清单里
}

// ToManifest 序列化成 Application 的 component 片段（ReplicaKey 被省略）。
func (c Component) ToManifest() map[string]any {
	out := map[string]any{"name": c.Name, "type": c.Type}
	if len(c.Properties) > 0 {
		out["properties"] = c.Properties
	}
	if len(c.Traits) > 0 {
		traits := make([]map[string]any, 0, len(c.Traits))
		for _, t := range c.Traits {
			item := map[string]any{"type": t.Type}
			if len(t.Properties) > 0 {
				item["properties"] = t.Properties
			}
			traits = append(traits, item)
		}
		out["traits"] = traits
	}
	if len(c.DependsOn) > 0 {
		out["dependsOn"] = c.DependsOn
	}
	if len(c.Scopes) > 0 {
		out["scopes"] = c.Scopes
	}
	return out
}

// Application 是 OAM Application。
type Application struct {
	Components []Component
	Policies   []string
	Workflow   *string
}

// Validate 校验 components 必填、名字唯一、dependsOn 指向存在的组件。
func (a Application) Validate() []string {
	var errs []string
	if len(a.Components) == 0 {
		errs = append(errs, "spec.components is required")
	}
	names := map[string]bool{}
	for _, c := range a.Components {
		if c.Name == "" || c.Type == "" {
			errs = append(errs, "each component requires name and type")
		}
		if names[c.Name] {
			errs = append(errs, "component names must be unique")
		}
		names[c.Name] = true
	}
	for _, c := range a.Components {
		for _, dep := range c.DependsOn {
			if !names[dep] || dep == c.Name {
				errs = append(errs, fmt.Sprintf("component %q dependsOn unknown %q", c.Name, dep))
			}
		}
	}
	return errs
}

// Render 有 workflow 时不下发资源，只产出 AppRevision 的渲染结果。
func (a Application) Render() ([]map[string]any, map[string]any) {
	var resources []map[string]any
	for _, c := range a.Components {
		resources = append(resources, c.ToManifest())
	}
	revision := map[string]any{"components": resources}
	if len(a.Policies) > 0 {
		revision["policies"] = a.Policies
	}
	if a.Workflow != nil {
		revision["workflow"] = map[string]any{"ref": *a.Workflow}
		return nil, revision
	}
	return resources, revision
}

// DefinitionReference 对应 common.DefinitionReference。
type DefinitionReference struct {
	Name    string
	Version string
}

// ResolveVersion 未指定 version 时取**第一个**可用版本（源码注释口径）。
func (r DefinitionReference) ResolveVersion(available []string) (string, error) {
	if r.Version != "" {
		for _, v := range available {
			if v == r.Version {
				return v, nil
			}
		}
		return "", fmt.Errorf("%w: version %q not served by %q", OamError, r.Version, r.Name)
	}
	if len(available) == 0 {
		return "", fmt.Errorf("%w: definition %q serves no version", OamError, r.Name)
	}
	return available[0], nil
}

// RevisionHash 用「排序后的键值拼接 + sha256 前 16 位」代替官方算法（本轮未读官方实现，
// 仅用于判等断言）。
func RevisionHash(spec map[string]string) string {
	keys := make([]string, 0, len(spec))
	for k := range spec {
		keys = append(keys, k)
	}
	sortStrings(keys)
	var b strings.Builder
	for _, k := range keys {
		b.WriteString(k)
		b.WriteString("=")
		b.WriteString(spec[k])
		b.WriteString(";")
	}
	sum := sha256.Sum256([]byte(b.String()))
	return fmt.Sprintf("%x", sum)[:16]
}

// DefinitionRevision 对应 v1beta1 DefinitionRevision。
type DefinitionRevision struct {
	Name           string
	Revision       int
	DefinitionType string
	SnapshotField  string
	Hash           string
}

// Validate 校验 definitionType 枚举、快照字段与类型一致、revision >= 1。
func (r DefinitionRevision) Validate() []string {
	var errs []string
	if !contains(DefinitionTypes, r.DefinitionType) {
		errs = append(errs, fmt.Sprintf("definitionType must be one of %v", DefinitionTypes))
		return errs
	}
	expected := strings.ToLower(r.DefinitionType[:1]) + r.DefinitionType[1:] + "Definition"
	if r.SnapshotField != expected {
		errs = append(errs, fmt.Sprintf("%s revisions must fill %q, got %q",
			r.DefinitionType, expected, r.SnapshotField))
	}
	if r.Revision < 1 {
		errs = append(errs, "revision must be >= 1")
	}
	return errs
}

// NextRevision spec 变化才递增；未变则沿用旧 revision。
func NextRevision(previous *DefinitionRevision, hash string) (int, string) {
	if previous != nil && previous.Hash == hash {
		return previous.Revision, hash
	}
	if previous == nil {
		return 1, hash
	}
	return previous.Revision + 1, hash
}

// ExposeSourceValues 未设置即暴露；maskPaths 按点号路径打码。
func ExposeSourceValues(policy map[string]any, consumed map[string]any) map[string]any {
	out := map[string]any{}
	hide := false
	if expose, ok := policy["exposeConsumedValues"].(bool); ok && !expose {
		hide = true
	}
	for k, v := range consumed {
		if hide {
			out[k] = "***"
		} else {
			out[k] = v
		}
	}
	if raw, ok := policy["maskPaths"].([]string); ok {
		for _, path := range raw {
			head, rest := path, ""
			if i := strings.Index(path, "."); i >= 0 {
				head, rest = path[:i], path[i+1:]
			}
			if _, ok := out[head]; !ok {
				continue
			}
			if rest == "" {
				out[head] = "***"
			} else if nested, ok := out[head].(map[string]any); ok {
				out[head] = maskNested(nested, rest)
			}
		}
	}
	return out
}

func maskNested(node map[string]any, rest string) map[string]any {
	out := map[string]any{}
	for k, v := range node {
		out[k] = v
	}
	head, tail := rest, ""
	if i := strings.Index(rest, "."); i >= 0 {
		head, tail = rest[:i], rest[i+1:]
	}
	if _, ok := out[head]; !ok {
		return out
	}
	if tail == "" {
		out[head] = "***"
	} else if nested, ok := out[head].(map[string]any); ok {
		out[head] = maskNested(nested, tail)
	}
	return out
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}
