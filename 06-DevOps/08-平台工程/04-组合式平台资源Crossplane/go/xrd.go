// Package main 转写 crossplane.py：XRD / Composition 的校验与组合选择（Go 侧）。
package main

import (
	"errors"
	"fmt"
	"strings"
)

// CrossplaneError 表示定义非法或选择失败。
var CrossplaneError = errors.New("crossplane error")

// Scopes 是 CompositeResourceScope 的三值；默认 LegacyCluster。
var Scopes = []string{"LegacyCluster", "Namespaced", "Cluster"}

const (
	DefaultScope         = "LegacyCluster"
	DefaultDeletePolicy  = "Background"
	DefaultUpdatePolicy  = "Automatic"
	PipelineMinItems     = 1
	PipelineMaxItems     = 99
)

// Names 对应 CustomResourceDefinitionNames。
type Names struct {
	Kind     string
	Plural   string
	Singular string
}

// Version 是 XRD 的一个版本。
type Version struct {
	Name          string
	Referenceable bool
	Served        bool
}

// Xrd 是 CompositeResourceDefinition。
type Xrd struct {
	Name                            string
	Group                           string
	Names                           Names
	Versions                        []Version
	Scope                           string
	ClaimNames                      *Names
	ConnectionSecretKeys            []string
	DefaultCompositionRef           string
	EnforcedCompositionRef          string
	DefaultCompositeDeletePolicy    string
	DefaultCompositionUpdatePolicy  string
}

// OffersClaim 等价于官方 OffersClaim()：ClaimNames 非空。
func (x Xrd) OffersClaim() bool { return x.ClaimNames != nil }

// CompositeGVK 对应 GetCompositeGroupVersionKind：取**最后一个** referenceable 版本。
func (x Xrd) CompositeGVK() [3]string {
	version := ""
	for _, v := range x.Versions { // 官方实现没有 break
		if v.Referenceable {
			version = v.Name
		}
	}
	return [3]string{x.Group, version, x.Names.Kind}
}

// ClaimGVK 不提供 claim 时返回空 GVK。
func (x Xrd) ClaimGVK() [3]string {
	if !x.OffersClaim() {
		return [3]string{"", "", ""}
	}
	gvk := x.CompositeGVK()
	return [3]string{gvk[0], gvk[1], x.ClaimNames.Kind}
}

// Validate 复刻 CEL 规则与命名约束。
func (x Xrd) Validate() []string {
	var errs []string
	if !contains(Scopes, x.Scope) {
		errs = append(errs, fmt.Sprintf("scope must be one of %v", Scopes))
	}
	if x.Name != x.Names.Plural+"."+x.Group {
		errs = append(errs, "metadata.name must equal <names.plural>.<group>")
	}
	if x.Names.Plural != strings.ToLower(x.Names.Plural) {
		errs = append(errs, "names.plural must be lowercase")
	}
	if x.Names.Singular != "" && x.Names.Singular != strings.ToLower(x.Names.Singular) {
		errs = append(errs, "names.singular must be lowercase")
	}
	if x.Scope != "LegacyCluster" && x.ClaimNames != nil {
		errs = append(errs, "Only LegacyCluster composite resources can offer claims")
	}
	if x.Scope != "LegacyCluster" && len(x.ConnectionSecretKeys) > 0 {
		errs = append(errs, "Only LegacyCluster composite resources support connection secrets")
	}
	if len(x.Versions) == 0 {
		errs = append(errs, "at least one version is required")
	}
	ref := false
	for _, v := range x.Versions {
		if v.Referenceable {
			ref = true
		}
	}
	if len(x.Versions) > 0 && !ref {
		errs = append(errs, "at least one version must be referenceable")
	}
	return errs
}

// Composition 是把 XR 落到具体资源上的实现。
type Composition struct {
	Name               string
	CompositeTypeRef   [2]string
	Pipeline           []string
	Mode               string
	Labels             map[string]string
}

// Validate 校验 mode 与 pipeline 长度、step 名唯一。
func (c Composition) Validate() []string {
	var errs []string
	if c.Mode != "Pipeline" {
		errs = append(errs, "mode must be one of [Pipeline]")
	}
	if len(c.Pipeline) < PipelineMinItems || len(c.Pipeline) > PipelineMaxItems {
		errs = append(errs, fmt.Sprintf("pipeline length must be in [%d, %d]", PipelineMinItems, PipelineMaxItems))
	}
	seen := map[string]bool{}
	for _, s := range c.Pipeline {
		if seen[s] {
			errs = append(errs, "pipeline step names must be unique (listMapKey=step)")
			break
		}
		seen[s] = true
	}
	if c.CompositeTypeRef[0] == "" || c.CompositeTypeRef[1] == "" {
		errs = append(errs, "compositeTypeRef requires both apiVersion and kind")
	}
	return errs
}

// Composite 是一个复合资源实例。
type Composite struct {
	Name                  string
	CompositionRef        string
	CompositionSelector   map[string]string
	CompositeDeletePolicy string
	UpdatePolicy          string
}

// SelectComposition 选择顺序：enforced > selector > 实例 ref > XRD 默认。
func SelectComposition(x Xrd, c Composite, comps []Composition) (Composition, error) {
	if x.EnforcedCompositionRef != "" {
		for _, comp := range comps {
			if comp.Name == x.EnforcedCompositionRef {
				return comp, nil
			}
		}
		return Composition{}, fmt.Errorf("%w: enforced composition %q not found", CrossplaneError, x.EnforcedCompositionRef)
	}
	if len(c.CompositionSelector) > 0 {
		var matched []Composition
		for _, comp := range comps {
			ok := true
			for k, v := range c.CompositionSelector {
				if comp.Labels[k] != v {
					ok = false
				}
			}
			if ok {
				matched = append(matched, comp)
			}
		}
		if len(matched) == 0 {
			return Composition{}, fmt.Errorf("%w: compositionSelector matched no composition", CrossplaneError)
		}
		if len(matched) > 1 {
			return Composition{}, fmt.Errorf("%w: compositionSelector matched %d compositions", CrossplaneError, len(matched))
		}
		return matched[0], nil
	}
	if c.CompositionRef != "" {
		for _, comp := range comps {
			if comp.Name == c.CompositionRef {
				return comp, nil
			}
		}
		return Composition{}, fmt.Errorf("%w: compositionRef %q not found", CrossplaneError, c.CompositionRef)
	}
	if x.DefaultCompositionRef != "" {
		for _, comp := range comps {
			if comp.Name == x.DefaultCompositionRef {
				return comp, nil
			}
		}
		return Composition{}, fmt.Errorf("%w: default composition %q not found", CrossplaneError, x.DefaultCompositionRef)
	}
	return Composition{}, fmt.Errorf("%w: no composition could be selected", CrossplaneError)
}

// FilterConnectionSecret 白名单过滤；空名单表示全部发布。
func FilterConnectionSecret(allowed []string, produced map[string]string) map[string]string {
	out := map[string]string{}
	if len(allowed) == 0 {
		for k, v := range produced {
			out[k] = v
		}
		return out
	}
	for k, v := range produced {
		if contains(allowed, k) {
			out[k] = v
		}
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
