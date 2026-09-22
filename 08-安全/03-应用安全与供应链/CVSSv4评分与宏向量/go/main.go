// CVSS v4.0 演示：同一条漏洞在不同部署环境下的分数变化，
// 以及「要求类指标在某些等价类里完全不起作用」这类反直觉行为。
package main

import "fmt"

type sample struct {
	title  string
	vector string
}

func demo() {
	fmt.Println("CVSS v4.0：宏向量查表 + 类内插值")
	samples := []sample{
		{"网络可达、无需权限、全量影响",
			"CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H"},
		{"同上，但威胁成熟度 E:U",
			"CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/E:U"},
		{"同上，环境里后续系统完整性被打到 Safety（MSI:S）",
			"CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/E:A/MSI:S"},
		{"只影响脆弱系统本身",
			"CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"},
		{"物理接触 + 高权限 + 需要用户交互",
			"CVSS:4.0/AV:P/AC:H/AT:P/PR:H/UI:A/VC:L/VI:L/VA:L/SC:L/SI:L/SA:L/E:U/CR:L/IR:L/AR:L"},
	}
	for _, s := range samples {
		sel, err := ParseVector(s.vector)
		if err != nil {
			panic(err)
		}
		mv := MacroVector(sel)
		fmt.Printf("  %s\n", s.title)
		fmt.Printf("    宏向量 %s  查表值 %-4.1f  最终 %-4.1f  %s\n",
			mv, lookup[mv], Score(sel), Severity(Score(sel)))
	}

	fmt.Println()
	fmt.Println("降低安全要求 CR/IR/AR（影响是 H 时有效）")
	base := "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/CR:%c/IR:%c/AR:%c"
	for _, req := range []string{"HHH", "MMM", "LLL"} {
		v := fmt.Sprintf(base, req[0], req[1], req[2])
		sel, _ := ParseVector(v)
		fmt.Printf("  CR/IR/AR = %s -> %-4.1f (宏向量 %s)\n", req, Score(sel), MacroVector(sel))
	}

	fmt.Println()
	fmt.Println("反例：VC/VI/VA 都不是 H（EQ3=2）时 CR/IR/AR 完全不起作用")
	low := "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:L/VI:L/VA:L/SC:N/SI:N/SA:N/CR:%c/IR:%c/AR:%c"
	for _, req := range []string{"HHH", "MMM", "LLL"} {
		v := fmt.Sprintf(low, req[0], req[1], req[2])
		sel, _ := ParseVector(v)
		fmt.Printf("  CR/IR/AR = %s -> %-4.1f\n", req, Score(sel))
	}

	fmt.Println()
	fmt.Println("环境修正能把「没有后续影响」的漏洞抬到 10.0")
	v := "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/MSC:H/MSI:S/MSA:S"
	sel, _ := ParseVector(v)
	fmt.Printf("  基础 SC/SI/SA 全 N + MSC:H/MSI:S/MSA:S -> %.1f (宏向量 %s)\n",
		Score(sel), MacroVector(sel))
}

func main() {
	demo()
}
