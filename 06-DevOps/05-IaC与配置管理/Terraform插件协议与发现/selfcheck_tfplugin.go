// tfplugin 自检：与 Python 版同口径，全部基于实际读过的官方原文。
package main

import (
	"fmt"
	"strings"
)

var n int
var fails []string

func check(label string, cond bool, detail string) {
	n++
	if !cond {
		fails = append(fails, label+"  "+detail)
	}
}

func sat(ver, c string) bool {
	ok, err := Satisfies(ver, c)
	if err != nil {
		panic(err)
	}
	return ok
}

func sel(c string, ins []string, reg []RegEntry, lock string, hasLock bool) (string, string) {
	v, s, err := SelectVersion(c, ins, reg, lock, hasLock)
	if err != nil {
		panic(err)
	}
	return v, s
}

func main() {
	// ---- 1. 版本约束 ----
	check("A1 ~> 允许最右分量递增", sat("1.0.9", "~> 1.0.4"), "")
	check("A2 ~> 1.0.4 挡住 1.1.0", !sat("1.1.0", "~> 1.0.4"), "")
	check("A3 ~> 1.0.4 挡住 1.0.3", !sat("1.0.3", "~> 1.0.4"), "")
	check("A4 ~> 1.0 允许 1.9.0", sat("1.9.0", "~> 1.0"), "")
	check("A5 ~> 1.0 挡住 2.0.0", !sat("2.0.0", "~> 1.0"), "")
	check("A6 >= 下限", sat("3.2.0", ">= 1.0") && !sat("0.9.9", ">= 1.0"), "")
	check("A7 逗号分隔是与", sat("1.5.0", ">= 1.0, < 2.0") && !sat("2.0.0", ">= 1.0, < 2.0"), "")
	check("A8 != 生效", !sat("1.2.3", "!= 1.2.3"), "")
	check("A9 = 精确", sat("1.2.3", "= 1.2.3") && !sat("1.2.4", "= 1.2.3"), "")
	check("A10 1.0 < 1.0.1", cmpVer(ParseVersion("1.0"), ParseVersion("1.0.1")) < 0, "")

	// ---- 2. 协议主版本兼容性 ----
	m := CliProtocolMajors("1.0")
	check("B1 CLI 1.0 支持 v5 与 v6", m[5] && m[6], "")
	m = CliProtocolMajors("1.9.8")
	check("B2 CLI 1.9 支持 v5 与 v6", m[5] && m[6], "")
	m = CliProtocolMajors("0.12")
	check("B3 CLI 0.12 只支持 v5", m[5] && !m[6], "")
	check("B4 CLI 0.11 一个都不支持", len(CliProtocolMajors("0.11")) == 0, "")

	// ---- 3. 协商 ----
	got, err := Negotiate([]int{6, 3}, [][2]int{{5, 2}, {6, 1}})
	check("C1 双方都支持 v6 时取 6", err == nil && got[0] == 6, fmt.Sprint(got, err))
	got, err = Negotiate([]int{6, 3}, [][2]int{{5, 2}})
	check("C2 插件只有 v5 时降级到 5", err == nil && got == [2]int{5, 2}, fmt.Sprint(got, err))
	got, err = Negotiate([]int{6, 3}, [][2]int{{6, 5}})
	check("C3 次版本取双方较小", err == nil && got == [2]int{6, 3}, fmt.Sprint(got, err))
	_, err = Negotiate([]int{6, 0}, [][2]int{{7, 0}})
	check("C4 无共同主版本报错", err != nil, fmt.Sprint(err))
	got, err = Negotiate([]int{6, 0}, [][2]int{{5, 9}, {6, 0}})
	check("C5 有 v6 时不选 v5", err == nil && got[0] == 6, fmt.Sprint(got, err))

	// ---- 4. 版本选择三条规则 ----
	reg := []RegEntry{{"2.0.0", 6}, {"2.1.0", 6}}
	v, s := sel(">= 1.0", []string{"1.2.0", "1.5.0"}, reg, "", false)
	check("D1 已安装优先且取最新", v == "1.5.0" && s == "installed", v+"/"+s)
	v, s = sel(">= 1.0", nil, reg, "", false)
	check("D2 无已安装时取 registry 最新", v == "2.1.0" && s == "registry", v+"/"+s)
	v, s = sel(">= 1.0", []string{"1.9.0"}, reg, "1.4.0", true)
	check("D3 lock 优先于一切", v == "1.4.0" && s == "locked", v+"/"+s)
	v, s = sel(">= 2.0", []string{"1.9.0"}, reg, "1.4.0", true)
	check("D4 lock 不满足约束则失效", v == "2.1.0" && s == "registry", v+"/"+s)
	v, s = sel(">= 3.0", []string{"1.0.0"}, reg, "", false)
	check("D5 都找不到则 failed", v == "" && s == "failed", v+"/"+s)
	v, s = sel(">= 2.0", []string{"1.5.0"}, []RegEntry{{"2.1.0", 6}, {"2.2.0", 6}}, "", false)
	check("D6 不可接受的已安装被忽略", v == "2.2.0" && s == "registry", v+"/"+s)

	// ---- 5. 协议版本参与 registry 筛选 ----
	reg2 := []RegEntry{{"1.0.0", 5}, {"2.0.0", 6}, {"3.0.0", 6}}
	v, s = mustSel(">= 1.0", nil, reg2, "1.9.8")
	check("E1 CLI 1.x 能看见 v6 插件", v == "3.0.0" && s == "registry", v+"/"+s)
	v, s = mustSel(">= 1.0", nil, reg2, "0.12")
	check("E2 CLI 0.12 只能看见 v5 插件", v == "1.0.0" && s == "registry", v+"/"+s)
	v, s = mustSel(">= 2.5", nil, reg2, "1.9.8")
	check("E3 约束与协议共同作用", v == "3.0.0" && s == "registry", v+"/"+s)

	// ---- 6. go-plugin 握手 ----
	ok, why := Handshake("TF_PLUGIN_MAGIC_COOKIE=d602bf8f\n1|6|unix|/tmp/p\n",
		"TF_PLUGIN_MAGIC_COOKIE", "d602bf8f", 6, 6)
	check("F1 cookie 与协议都对则通过", ok && why == "ok", why)
	ok, why = Handshake("some random binary output\n", "TF_PLUGIN_MAGIC_COOKIE",
		"d602bf8f", 6, 6)
	check("F2 cookie 不匹配拦下非插件二进制", !ok && contains(why, "magic cookie"), why)
	ok, why = Handshake("TF_PLUGIN_MAGIC_COOKIE=d602bf8f\n", "TF_PLUGIN_MAGIC_COOKIE",
		"d602bf8f", 5, 6)
	check("F3 协议版本不一致报错", !ok && contains(why, "incompatible"), why)

	// ---- 7. 其它 ----
	check("G1 Provider 的 8 个核心 RPC 全在表内",
		len(GoodRPCs) == 8 && hasStr(GoodRPCs, "PlanResourceChange") &&
			hasStr(GoodRPCs, "ImportResourceState"), "")
	got, err = Negotiate([]int{5, 0}, [][2]int{{5, 0}, {6, 0}})
	check("G2 CLI 只声明 v5 时不会协商出 v6", err == nil && got[0] == 5, fmt.Sprint(got, err))

	fmt.Printf("checks=%d fail=%d\n", n, len(fails))
	for _, f := range fails {
		fmt.Println("  FAIL", f)
	}
}

func mustSel(c string, ins []string, reg []RegEntry, cli string) (string, string) {
	v, s, err := SelectWithProtocol(c, ins, reg, cli, "", false)
	if err != nil {
		panic(err)
	}
	return v, s
}

func contains(s, sub string) bool {
	return strings.Contains(s, sub)
}

func hasStr(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}
