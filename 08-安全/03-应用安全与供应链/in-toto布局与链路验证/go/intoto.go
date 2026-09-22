// in-toto 布局与链路（layout / link）验证。
//
// 规范 in-toto/specification：
//   4.3 layout 格式（steps / inspections / keys / expires）
//   4.3.1 threshold 与 expected_command（命令不符只告警）
//   4.4 link 文件名 [name].[KEYID-PREFIX].link，KEYID-PREFIX 取 keyid 前六字节
//   4.5.1 sublayout 递归验证后向上呈现「虚拟 link」：
//        materials 取子布局首个 step 的 materials，products 取末个 step 的 products
package main

import (
	"fmt"
	"strings"
)

// Link 一份 link 元数据；Sublayout 非空时它是个子布局
type Link struct {
	Name      string
	Materials map[string]string
	Products  map[string]string
	Command   string
	Signer    string
	Byproducts map[string]string
	Sublayout *Layout
	SubLinks  map[string][]*Link
}

// GetMaterials 实现 Artifacts
func (l *Link) GetMaterials() map[string]string { return l.Materials }

// GetProducts 实现 Artifacts
func (l *Link) GetProducts() map[string]string { return l.Products }

// IsSublayout 是否是子布局
func (l *Link) IsSublayout() bool { return l.Sublayout != nil }

// Step 布局里的一个步骤
type Step struct {
	Name              string
	Threshold         int
	ExpectedMaterials []string
	ExpectedProducts  []string
	Pubkeys           []string
	ExpectedCommand   string
}

// Inspection 客户端验收步骤
type Inspection struct {
	Name              string
	ExpectedMaterials []string
	ExpectedProducts  []string
	Run               string
}

// Layout 整份布局
type Layout struct {
	Steps       []Step
	Inspections []Inspection
}

// Result 验证结果
type Result struct {
	Errors   []string
	Warnings []string
}

// OK 是否通过
func (r *Result) OK() bool { return len(r.Errors) == 0 }

// LinkFilename 规范 4.4：keyid 前六字节 = 12 个十六进制字符
func LinkFilename(name, keyidHex string) string {
	prefix := keyidHex
	if len(prefix) > 12 {
		prefix = prefix[:12]
	}
	return fmt.Sprintf("%s.%s.link", name, prefix)
}

func authorized(step Step, links []*Link) (good []*Link, bad []string) {
	for _, l := range links {
		hit := false
		for _, k := range step.Pubkeys {
			if l.Signer == k {
				hit = true
			}
		}
		if hit {
			good = append(good, l)
		} else {
			bad = append(bad, l.Signer)
		}
	}
	return good, bad
}

func agree(links []*Link) bool {
	if len(links) == 0 {
		return true
	}
	first := links[0]
	for _, l := range links[1:] {
		if !sameMap(l.Materials, first.Materials) || !sameMap(l.Products, first.Products) {
			return false
		}
	}
	return true
}

func sameMap(a, b map[string]string) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

func flatten(linksByStep map[string][]*Link) map[string]Artifacts {
	out := map[string]Artifacts{}
	for name, links := range linksByStep {
		if len(links) > 0 {
			out[name] = links[0]
		}
	}
	return out
}

// VerifyStep 验证一个 step，返回验证时使用的那份（可能是虚拟的）link
func VerifyStep(step Step, linksByStep map[string][]*Link, res *Result, prefix string) *Link {
	links := linksByStep[step.Name]
	good, bad := authorized(step, links)
	for _, signer := range bad {
		res.Errors = append(res.Errors, fmt.Sprintf("%s: %s 的签名者 %s 不在 pubkeys 里",
			prefix, step.Name, signer))
	}
	if len(good) < step.Threshold {
		res.Errors = append(res.Errors, fmt.Sprintf("%s: %s 的 link 数 %d < 阈值 %d",
			prefix, step.Name, len(good), step.Threshold))
		return nil
	}
	if step.Threshold > 1 && !agree(good) {
		res.Errors = append(res.Errors, fmt.Sprintf(
			"%s: %s 的 %d 份 link 内容不一致（阈值要求结果相同）", prefix, step.Name, len(good)))
		return nil
	}
	link := good[0]
	if link.IsSublayout() {
		virtual := VerifyLayout(link.Sublayout, link.SubLinks, res, prefix+step.Name+"/")
		if virtual == nil {
			return nil
		}
		link = virtual
	}
	merged := map[string][]*Link{}
	for k, v := range linksByStep {
		merged[k] = v
	}
	merged[step.Name] = []*Link{link}
	for _, pair := range []struct {
		kind  string
		rules []string
	}{{"materials", step.ExpectedMaterials}, {"products", step.ExpectedProducts}} {
		passed, err, _ := VerifyExpected(pair.rules, sideOf(link, pair.kind), pair.kind,
			link, flatten(merged))
		if !passed {
			res.Errors = append(res.Errors, fmt.Sprintf("%s: %s 的 expected_%s 未通过 —— %s",
				prefix, step.Name, pair.kind, err.Error()))
		}
	}
	if step.ExpectedCommand != "" && link.Command != "" && step.ExpectedCommand != link.Command {
		res.Warnings = append(res.Warnings, fmt.Sprintf(
			"%s: %s 的实际命令 %q 与期望 %q 不一致（按规范只告警）",
			prefix, step.Name, link.Command, step.ExpectedCommand))
	}
	return link
}

// VerifyLayout 验证整份布局，成功时返回虚拟 link 供上层 MATCH 使用
func VerifyLayout(layout *Layout, linksByStep map[string][]*Link, res *Result,
	prefix string) *Link {
	resolved := map[string]*Link{}
	order := []string{}
	for _, step := range layout.Steps {
		if link := VerifyStep(step, linksByStep, res, prefix); link != nil {
			resolved[step.Name] = link
			order = append(order, step.Name)
		}
	}
	if len(order) == 0 {
		return nil
	}
	first := resolved[order[0]]
	last := resolved[order[len(order)-1]]
	return &Link{Name: order[0], Materials: first.Materials, Products: last.Products,
		Command: last.Command, Signer: last.Signer}
}

// Verify 对外入口
func Verify(layout *Layout, linksByStep map[string][]*Link) *Result {
	res := &Result{}
	VerifyLayout(layout, linksByStep, res, "")
	for _, insp := range layout.Inspections {
		link := &Link{Name: insp.Name}
		if ls, ok := linksByStep[insp.Name]; ok && len(ls) > 0 {
			link = ls[0]
		}
		for _, pair := range []struct {
			kind  string
			rules []string
		}{{"materials", insp.ExpectedMaterials}, {"products", insp.ExpectedProducts}} {
			passed, err, _ := VerifyExpected(pair.rules, sideOf(link, pair.kind), pair.kind,
				link, flatten(linksByStep))
			if !passed {
				res.Errors = append(res.Errors, fmt.Sprintf(
					"inspection %s 的 expected_%s 未通过 —— %s", insp.Name, pair.kind, err.Error()))
			}
		}
	}
	return res
}

// Describe 把结果渲染成多行文本
func Describe(res *Result) string {
	var b strings.Builder
	fmt.Fprintf(&b, "通过: %v", res.OK())
	for _, e := range res.Errors {
		fmt.Fprintf(&b, "\n  ERROR %s", e)
	}
	for _, w := range res.Warnings {
		fmt.Fprintf(&b, "\n  WARN  %s", w)
	}
	return b.String()
}
