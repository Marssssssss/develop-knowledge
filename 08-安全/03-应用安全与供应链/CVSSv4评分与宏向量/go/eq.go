// CVSS v4.0 评分：宏向量（MacroVector）+ 类内插值。
//
// 解析与等价类（EQ）判定。
//
// 转写官方 cvss_score.js：ParseVector 对应向量串解析，Resolve 对应 m()，
// eq1..eq6 对应 Table 24-30 的约束条件。
package main

import (
	"errors"
	"fmt"
	"strings"
)
// Sel 是一条向量的全部指标取值，未给出的用 "X"（Not Defined）
type Sel map[string]string

// ParseVector 解析 CVSS:4.0/AV:N/... 形式的向量串
func ParseVector(text string) (Sel, error) {
	text = strings.TrimSpace(text)
	if !strings.HasPrefix(text, "CVSS:4.0") {
		return nil, errors.New("向量串必须以 CVSS:4.0 开头")
	}
	sel := Sel{}
	for _, v := range valid {
		sel[v.name] = "X"
	}
	body := strings.TrimPrefix(text, "CVSS:4.0")
	body = strings.TrimPrefix(body, "/")
	for _, part := range strings.Split(body, "/") {
		if part == "" {
			continue
		}
		kv := strings.SplitN(part, ":", 2)
		if len(kv) != 2 {
			return nil, fmt.Errorf("片段缺少冒号: %q", part)
		}
		allowed := ""
		for _, v := range valid {
			if v.name == kv[0] {
				allowed = v.values
			}
		}
		if allowed == "" {
			return nil, fmt.Errorf("未知指标: %q", kv[0])
		}
		if _, ok := sel[kv[0]]; ok && sel[kv[0]] != "X" {
			return nil, fmt.Errorf("指标重复: %q", kv[0])
		}
		if !strings.Contains(allowed, kv[1]) {
			return nil, fmt.Errorf("%s 的取值 %q 不合法", kv[0], kv[1])
		}
		sel[kv[0]] = kv[1]
	}
	for _, m := range baseMetrics {
		if sel[m] == "X" {
			return nil, fmt.Errorf("基础指标 %s 必填", m)
		}
	}
	return sel, nil
}

// Resolve 官方的 m()：E:X -> A，CR/IR/AR:X -> H，M 前缀修正指标不为 X 就覆盖
func Resolve(sel Sel, metric string) string {
	value := sel[metric]
	if metric == "E" && value == "X" {
		return "A"
	}
	if (metric == "CR" || metric == "IR" || metric == "AR") && value == "X" {
		return "H"
	}
	if mod, ok := sel["M"+metric]; ok && mod != "X" {
		return mod
	}
	return value
}

func eq1(sel Sel) int {
	av, pr, ui := Resolve(sel, "AV"), Resolve(sel, "PR"), Resolve(sel, "UI")
	any := av == "N" || pr == "N" || ui == "N"
	all := av == "N" && pr == "N" && ui == "N"
	switch {
	case all:
		return 0
	case any && av != "P":
		return 1
	default:
		return 2
	}
}

func eq2(sel Sel) int {
	if Resolve(sel, "AC") == "L" && Resolve(sel, "AT") == "N" {
		return 0
	}
	return 1
}

func eq3(sel Sel) int {
	vc, vi, va := Resolve(sel, "VC"), Resolve(sel, "VI"), Resolve(sel, "VA")
	if vc == "H" && vi == "H" {
		return 0
	}
	if vc == "H" || vi == "H" || va == "H" {
		return 1
	}
	return 2
}

// msiMsa 规范 Table 15：MSI/MSA 为 X 时回落到 SI/SA
func msiMsa(sel Sel) (string, string) {
	msi, msa := sel["MSI"], sel["MSA"]
	if msi == "X" {
		msi = sel["SI"]
	}
	if msa == "X" {
		msa = sel["SA"]
	}
	return msi, msa
}

func eq4(sel Sel) int {
	msi, msa := msiMsa(sel)
	if msi == "S" || msa == "S" {
		return 0
	}
	sc, si, sa := Resolve(sel, "SC"), Resolve(sel, "SI"), Resolve(sel, "SA")
	if sc == "H" || si == "H" || sa == "H" {
		return 1
	}
	return 2
}

func eq5(sel Sel) int {
	return map[string]int{"A": 0, "P": 1, "U": 2}[Resolve(sel, "E")]
}

func eq6(sel Sel) int {
	cr, ir, ar := Resolve(sel, "CR"), Resolve(sel, "IR"), Resolve(sel, "AR")
	vc, vi, va := Resolve(sel, "VC"), Resolve(sel, "VI"), Resolve(sel, "VA")
	if (cr == "H" && vc == "H") || (ir == "H" && vi == "H") || (ar == "H" && va == "H") {
		return 0
	}
	return 1
}

