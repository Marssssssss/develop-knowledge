// in-toto 工件规则（Artifact Rules）引擎。
//
// 规范 in-toto/specification 4.3.3：规则挂在 step / inspection 的
// expected_materials 与 expected_products 上，顺序执行，像防火墙规则一样
// —— 被某条规则消费掉的工件会从队列里移除，后面的规则再也看不到它。
package main

import (
	"errors"
	"path/filepath"
	"strings"
)

const (
	matchRule    = "MATCH"
	createRule   = "CREATE"
	deleteRule   = "DELETE"
	modifyRule   = "MODIFY"
	allowRule    = "ALLOW"
	requireRule  = "REQUIRE"
	disallowRule = "DISALLOW"
)

// Rule 一条已解析的规则
type Rule struct {
	Kind      string
	Pattern   string
	SrcPrefix string
	DstKind   string
	DstPrefix string
	Step      string
	Name      string
}

// ParseRule 解析规则串
func ParseRule(text string) (Rule, error) {
	tokens := strings.Fields(text)
	if len(tokens) == 0 {
		return Rule{}, errors.New("空规则")
	}
	head := strings.ToUpper(tokens[0])
	if head == matchRule {
		if len(tokens) < 5 || strings.ToUpper(tokens[len(tokens)-2]) != "FROM" {
			return Rule{}, errors.New("MATCH 必须以 FROM <step> 结尾")
		}
		r := Rule{Kind: matchRule, Pattern: tokens[1], Step: tokens[len(tokens)-1]}
		i := 2
		if i < len(tokens) && strings.ToUpper(tokens[i]) == "IN" {
			r.SrcPrefix = tokens[i+1]
			i += 2
		}
		if i >= len(tokens) || strings.ToUpper(tokens[i]) != "WITH" {
			return Rule{}, errors.New("MATCH 缺少 WITH")
		}
		// 规范语法块写 (MATERIALS|PRODUCTS)，正文示例写 PRODUCT —— 单复数都接受
		switch strings.ToUpper(tokens[i+1]) {
		case "MATERIAL", "MATERIALS":
			r.DstKind = "MATERIALS"
		case "PRODUCT", "PRODUCTS":
			r.DstKind = "PRODUCTS"
		default:
			return Rule{}, errors.New("WITH 后面只能是 MATERIAL(S) 或 PRODUCT(S)")
		}
		i += 2
		if i < len(tokens) && strings.ToUpper(tokens[i]) == "IN" {
			r.DstPrefix = tokens[i+1]
			i += 2
		}
		if i != len(tokens)-2 {
			return Rule{}, errors.New("MATCH 参数多余")
		}
		return r, nil
	}
	if head == requireRule {
		if len(tokens) != 2 {
			return Rule{}, errors.New("REQUIRE 只接受一个工件名")
		}
		return Rule{Kind: requireRule, Name: tokens[1]}, nil
	}
	switch head {
	case createRule, deleteRule, modifyRule, allowRule, disallowRule:
		if len(tokens) != 2 {
			return Rule{}, errors.New(head + " 只接受一个 pattern")
		}
		return Rule{Kind: head, Pattern: tokens[1]}, nil
	}
	return Rule{}, errors.New("未知规则类型: " + tokens[0])
}

// StripPrefix 摘掉 IN 子句声明的前缀；摘不掉就原样返回
func StripPrefix(name, prefix string) string {
	if prefix == "" {
		return name
	}
	return strings.TrimPrefix(name, prefix)
}

// MatchNames 先用前缀过滤，再用 glob 匹配 pattern
func MatchNames(artifacts map[string]string, prefix, pattern string) map[string]string {
	out := map[string]string{}
	for name, digest := range artifacts {
		if prefix != "" && !strings.HasPrefix(name, prefix) {
			continue
		}
		if ok, _ := filepath.Match(pattern, StripPrefix(name, prefix)); ok {
			out[name] = digest
		}
	}
	return out
}

// Artifacts 是一侧的工件集合（step 的 materials 或 products）
type Artifacts interface {
	GetMaterials() map[string]string
	GetProducts() map[string]string
}

func sideOf(a Artifacts, kind string) map[string]string {
	if kind == "materials" {
		return a.GetMaterials()
	}
	return a.GetProducts()
}

// ApplyRule 在队列上执行一条规则，返回 (被消费的工件名, 错误)
func ApplyRule(rule Rule, queue map[string]string, kind string, link Artifacts,
	links map[string]Artifacts) ([]string, error) {
	other := "products"
	if kind == "materials" {
		other = "materials"
	}
	opposite := sideOf(link, other)

	switch rule.Kind {
	case allowRule, disallowRule:
		hit := MatchNames(queue, "", rule.Pattern)
		if rule.Kind == disallowRule {
			if len(hit) > 0 {
				names := make([]string, 0, len(hit))
				for n := range hit {
					names = append(names, n)
				}
				return nil, errors.New("DISALLOW " + rule.Pattern + " 命中了未被授权的工件 " +
					strings.Join(names, ","))
			}
			return nil, nil
		}
		consumed := make([]string, 0, len(hit))
		for n := range hit {
			consumed = append(consumed, n)
		}
		return consumed, nil

	case requireRule:
		// 规范伪代码只做存在性检查，不消费
		if _, ok := queue[rule.Name]; !ok {
			return nil, errors.New("REQUIRE " + rule.Name + " 不在剩余工件里")
		}
		return nil, nil

	case matchRule:
		dst, ok := links[rule.Step]
		if !ok {
			return nil, errors.New("MATCH 引用了不存在的步骤 " + rule.Step)
		}
		dstSide := sideOf(dst, "materials")
		if rule.DstKind == "PRODUCTS" {
			dstSide = sideOf(dst, "products")
		}
		byKey := map[string]string{}
		for name, digest := range dstSide {
			if rule.DstPrefix != "" && !strings.HasPrefix(name, rule.DstPrefix) {
				continue
			}
			byKey[StripPrefix(name, rule.DstPrefix)] = digest
		}
		consumed := []string{}
		for name, digest := range MatchNames(queue, rule.SrcPrefix, rule.Pattern) {
			key := StripPrefix(name, rule.SrcPrefix)
			if d, ok2 := byKey[key]; ok2 && d == digest {
				consumed = append(consumed, name)
			}
		}
		return consumed, nil

	case createRule, deleteRule:
		hit := MatchNames(queue, "", rule.Pattern)
		var consumed []string
		for name := range hit {
			if _, bad := opposite[name]; bad {
				return nil, errors.New(rule.Kind + " " + rule.Pattern + " 命中 " + name +
					"，但它同时也是 " + other)
			}
			consumed = append(consumed, name)
		}
		return consumed, nil

	case modifyRule:
		hit := MatchNames(queue, "", rule.Pattern)
		var consumed []string
		for name, digest := range hit {
			prev, ok := opposite[name]
			if !ok {
				return nil, errors.New("MODIFY " + rule.Pattern + " 命中 " + name +
					"，但它不在 " + other + " 里")
			}
			if prev == digest {
				return nil, errors.New("MODIFY " + rule.Pattern + " 命中 " + name + "，但哈希没变")
			}
			consumed = append(consumed, name)
		}
		return consumed, nil
	}
	return nil, errors.New("未知规则类型 " + rule.Kind)
}

// VerifyExpected 顺序执行一整串规则。
// 规范 4.3.3.1：规则列表末尾有一条隐式的 ALLOW *，
// 显式写 DISALLOW * 才能挡住「溜进来」的工件。
func VerifyExpected(texts []string, artifacts map[string]string, kind string,
	link Artifacts, links map[string]Artifacts) (bool, error, []string) {
	queue := map[string]string{}
	for k, v := range artifacts {
		queue[k] = v
	}
	for _, t := range texts {
		rule, err := ParseRule(t)
		if err != nil {
			return false, err, nil
		}
		consumed, err := ApplyRule(rule, queue, kind, link, links)
		if err != nil {
			left := make([]string, 0, len(queue))
			for n := range queue {
				left = append(left, n)
			}
			return false, err, left
		}
		for _, n := range consumed {
			delete(queue, n)
		}
	}
	left := make([]string, 0, len(queue))
	for n := range queue {
		left = append(left, n)
	}
	return true, nil, left
}
