// Package main 转写 catalog.py：软件目录实体模型的校验与关系推导（Go 侧）。
package main

import (
	"fmt"
	"regexp"
	"strings"
)

var (
	nameRe     = regexp.MustCompile(`^[a-zA-Z0-9]+(?:[-_.][a-zA-Z0-9]+)*$`)
	nsRe       = regexp.MustCompile(`^[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*$`)
	tagRe      = regexp.MustCompile(`^[a-z0-9:+#]+(?:-[a-z0-9:+#]+)*$`)
	keyNameRe  = regexp.MustCompile(`^[a-zA-Z0-9]+(?:[-_.][a-zA-Z0-9]+)*$`)
	domainRe   = regexp.MustCompile(`^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$`)
	maxName    = 63
	maxPrefix  = 253
)

// ValidName 对应 metadata.name：1..63，字母数字段由单个 [-_.] 分隔。
func ValidName(v string) bool {
	return v != "" && len(v) <= maxName && nameRe.MatchString(v)
}

// ValidNamespace 与 name 的差别：分隔符只有 '-'（不含 '_' 与 '.'）。
func ValidNamespace(v string) bool {
	return v != "" && len(v) <= maxName && nsRe.MatchString(v)
}

// ValidTag 强制小写，且额外允许 ':' '+' '#'。
func ValidTag(v string) bool {
	return v != "" && len(v) <= maxName && tagRe.MatchString(v)
}

// ValidKey 校验 labels/annotations 的键：可选小写域名前缀 + '/' + name。
func ValidKey(k string) bool {
	if k == "" {
		return false
	}
	name := k
	if i := strings.Index(k, "/"); i >= 0 {
		prefix := k[:i]
		name = k[i+1:]
		if prefix == "" || len(prefix) > maxPrefix || !domainRe.MatchString(prefix) {
			return false
		}
	}
	return name != "" && len(name) <= maxName && keyNameRe.MatchString(name)
}

// EntityRef 是解析后的实体引用；字符串形式统一小写 kind。
type EntityRef struct {
	Kind      string
	Namespace string
	Name      string
}

func (r EntityRef) String() string {
	return strings.ToLower(r.Kind) + ":" + r.Namespace + "/" + r.Name
}

var coreKinds = []string{"API", "Component", "Domain", "Group", "Location", "Resource", "System", "User"}

func canonicalKind(raw string) (string, bool) {
	for _, k := range coreKinds {
		if strings.EqualFold(k, raw) {
			return k, true
		}
	}
	return "", false
}

// ParseEntityRef 解析 `[<kind>:][<namespace>/]<name>`。
func ParseEntityRef(ref, defaultKind, defaultNamespace string) (EntityRef, error) {
	if ref == "" {
		return EntityRef{}, fmt.Errorf("entity reference must be a non-empty string")
	}
	kind := ""
	rest := ref
	if i := strings.Index(rest, ":"); i >= 0 {
		raw := rest[:i]
		rest = rest[i+1:]
		var ok bool
		kind, ok = canonicalKind(raw)
		if !ok {
			return EntityRef{}, fmt.Errorf("unknown kind %q in reference %q", raw, ref)
		}
	}
	ns := defaultNamespace
	name := rest
	if i := strings.LastIndex(rest, "/"); i >= 0 {
		raw := rest[:i]
		name = rest[i+1:]
		if raw == "" || name == "" || !ValidNamespace(raw) {
			return EntityRef{}, fmt.Errorf("malformed namespace/name in reference %q", ref)
		}
		ns = strings.ToLower(raw)
	}
	if !ValidName(name) {
		return EntityRef{}, fmt.Errorf("invalid name %q in reference %q", name, ref)
	}
	if kind == "" {
		var ok bool
		kind, ok = canonicalKind(defaultKind)
		if !ok {
			return EntityRef{}, fmt.Errorf("unknown default kind %q", defaultKind)
		}
	}
	return EntityRef{Kind: kind, Namespace: ns, Name: name}, nil
}

// RelationPair 是 relations.ts 里的一组对称关系。
type RelationPair struct {
	Forward   string
	Reverse   string
	FromKinds []string
	ToKinds   []string
}

// WellKnownRelations 取自官方 relations.ts 的 7 组关系对。
var WellKnownRelations = map[string]RelationPair{
	"ownedBy":     {"ownedBy", "ownerOf", coreKinds, []string{"Group", "User"}},
	"providesApi": {"providesApi", "apiProvidedBy", []string{"Component"}, []string{"API"}},
	"consumesApi": {"consumesApi", "apiConsumedBy", []string{"Component"}, []string{"API"}},
	"dependsOn":   {"dependsOn", "dependencyOf", []string{"Component", "Resource"}, []string{"Component", "Resource"}},
	"parentOf":    {"parentOf", "childOf", []string{"Group"}, []string{"Group"}},
	"memberOf":    {"memberOf", "hasMember", []string{"User"}, []string{"Group"}},
	"partOf":      {"partOf", "hasPart", []string{"Component", "API", "Resource", "System", "Domain"}, []string{"Component", "System", "Domain"}},
}

type specField struct {
	DefaultKind string
	Pair        string
	Reverse     bool
}

// SpecFields 把 spec 字段映射到关系对与方向。
var SpecFields = map[string]specField{
	"owner":          {"Group", "ownedBy", false},
	"system":         {"System", "partOf", false},
	"subcomponentOf": {"Component", "partOf", false},
	"providesApis":   {"API", "providesApi", false},
	"consumesApis":   {"API", "consumesApi", false},
	"dependsOn":      {"Component", "dependsOn", false},
	"dependencyOf":   {"Component", "dependsOn", true},
	"memberOf":       {"Group", "memberOf", false},
	"parent":         {"Group", "parentOf", true},
	"children":       {"Group", "parentOf", false},
	"domain":         {"Domain", "partOf", false},
}

// Entity 是待处理的目录实体。
type Entity struct {
	Kind      string
	Name      string
	Namespace string
	Spec      map[string][]string
}

func (e Entity) Ref() EntityRef {
	return EntityRef{Kind: e.Kind, Namespace: e.Namespace, Name: e.Name}
}

// Relation 是 (source, type, target) 三元组。
type Relation struct {
	Source EntityRef
	Type   string
	Target EntityRef
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

// DeduceRelations 推导实体的正向与反向关系。
func DeduceRelations(e Entity) ([]Relation, error) {
	var out []Relation
	for field, cfg := range SpecFields {
		pair := WellKnownRelations[cfg.Pair]
		srcType, tgtType := pair.Forward, pair.Reverse
		srcKinds, tgtKinds := pair.FromKinds, pair.ToKinds
		if cfg.Reverse {
			srcType, tgtType = pair.Reverse, pair.Forward
			srcKinds, tgtKinds = pair.ToKinds, pair.FromKinds
		}
		for _, raw := range e.Spec[field] {
			target, err := ParseEntityRef(raw, cfg.DefaultKind, e.Namespace)
			if err != nil {
				return nil, err
			}
			if !contains(srcKinds, e.Kind) || !contains(tgtKinds, target.Kind) {
				return nil, fmt.Errorf("relation %s forbids %s -> %s", srcType, e.Kind, target.Kind)
			}
			out = append(out,
				Relation{Source: e.Ref(), Type: srcType, Target: target},
				Relation{Source: target, Type: tgtType, Target: e.Ref()},
			)
		}
	}
	return out, nil
}
