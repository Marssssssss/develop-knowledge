// 自检入口(与 python/semver_check.py + oci_check.py 对应的关键断言子集)。
// OCI 侧断言在 oci_check.go, 同属 package main。
package main

import (
	"encoding/json"
	"fmt"
)

var (
	pass, fail int
	failed     []string
)

func check(label string, cond bool, detail string) {
	if cond {
		pass++
		return
	}
	fail++
	failed = append(failed, label+" "+detail)
	fmt.Printf("  FAIL %s %s\n", label, detail)
}

func eqS(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func lt(a, b string) bool { return MustVersion(a).Compare(MustVersion(b)) < 0 }

// makeManifest 组装一个最小 image manifest;extra 用于追加 subject / artifactType。
func makeManifest(cfgDigest, layerDigest string, extra map[string]interface{}) []byte {
	doc := map[string]interface{}{
		"schemaVersion": 2,
		"mediaType":     manifestMediaType,
		"config": map[string]interface{}{
			"mediaType": "application/vnd.oci.image.config.v1+json",
			"digest":    cfgDigest, "size": float64(2)},
		"layers": []interface{}{map[string]interface{}{
			"mediaType": "application/vnd.oci.image.layer.v1.tar",
			"digest":    layerDigest, "size": float64(5)}},
	}
	for k, v := range extra {
		doc[k] = v
	}
	return mustJSON(doc)
}

// pushImage 推一个最小镜像并返回 manifest digest(config 里带 tag 名以保证互不相同)。
func pushImage(reg *Registry, name, tag string) string {
	config := []byte(`{"v":"` + tag + `"}`)
	layer := []byte("layer-of-" + tag)
	cd, ld := MustDigest(config), MustDigest(layer)
	reg.PutBlob(name, config, cd)
	reg.PutBlob(name, layer, ld)
	return reg.PutManifest(name, makeManifest(cd, ld, nil), tag).Header("Docker-Content-Digest")
}

func decodeTags(raw []byte) []string {
	var body struct {
		Name string   `json:"name"`
		Tags []string `json:"tags"`
	}
	_ = json.Unmarshal(raw, &body)
	return body.Tags
}

func decodeIndex(raw []byte) []map[string]interface{} {
	var idx struct {
		Manifests []map[string]interface{} `json:"manifests"`
	}
	_ = json.Unmarshal(raw, &idx)
	return idx.Manifests
}

func main() {
	fmt.Println("SemVer 2.0.0 + OCI 制品语义自检(Go)")

	testParse()
	testPrecedence()
	testBump()
	testDigest()
	testRegistry()
	testDiscovery()
	testPromotion()

	fmt.Printf("\n断言 %d 通过 / %d 失败\n", pass, fail)
	if fail > 0 {
		fmt.Println("失败明细:")
		for _, f := range failed {
			fmt.Println("  - " + f)
		}
	}
}

// ============================ 1. 解析与合法性 ============================

func testParse() {
	fmt.Println("[1] 解析与合法性")
	check("基本形式", MustVersion("1.2.3").String() == "1.2.3", "")
	check("预发布", MustVersion("1.0.0-alpha.1").String() == "1.0.0-alpha.1", "")
	check("构建元数据", MustVersion("1.0.0-beta+exp.sha.5114f85").String() ==
		"1.0.0-beta+exp.sha.5114f85", "")
	check("官方例子 1.0.0-x-y-z.--", MustVersion("1.0.0-x-y-z.--").String() == "1.0.0-x-y-z.--", "")
	check("官方例子 1.0.0-0.3.7",
		eqS(MustVersion("1.0.0-0.3.7").Pre, []string{"0", "3", "7"}), "")

	for _, bad := range []string{"01.2.3", "1.02.3", "1.2.3-01", "1.2", "1.2.3.4",
		"1.2.3-alpha..1", "1.2.3+build..x", "v1.2.3", "=1.2.3", ""} {
		_, err := ParseVersion(bad)
		check("非法版本必须报错: "+bad, err != nil, "")
	}
}

// ============================ 2. 优先级比较 ============================

func testPrecedence() {
	fmt.Println("[2] 优先级比较")
	check("官方例子: 1.0.0 < 2.0.0 < 2.1.0 < 2.1.1",
		lt("1.0.0", "2.0.0") && lt("2.0.0", "2.1.0") && lt("2.1.0", "2.1.1"), "")
	check("预发布低于正规版本: 1.0.0-alpha < 1.0.0", lt("1.0.0-alpha", "1.0.0"), "")

	chain := []string{"1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
		"1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"}
	ordered := true
	for i := 1; i < len(chain); i++ {
		if !lt(chain[i-1], chain[i]) {
			ordered = false
		}
	}
	check("官方例子整链升序", ordered, "")

	check("beta.2 < beta.11(数字标识符按数值比)",
		lt("1.0.0-beta.2", "1.0.0-beta.11"), "")
	check("alpha.1 < alpha.beta(数字恒低于非数字)",
		lt("1.0.0-alpha.1", "1.0.0-alpha.beta"), "")
	check("alpha < alpha.1(前缀相等时标识符多者更高)",
		lt("1.0.0-alpha", "1.0.0-alpha.1"), "")
	check("ASCII 序: 1.0.0-Alpha < 1.0.0-alpha", lt("1.0.0-Alpha", "1.0.0-alpha"), "")
	check("构建元数据不参与比较: 1.0.0+a == 1.0.0+b",
		MustVersion("1.0.0+a").Compare(MustVersion("1.0.0+b")) == 0, "")
	check("1.9.0 < 1.10.0(数值递增)", lt("1.9.0", "1.10.0"), "")
	check("10.0.0 不小于 9.0.0(不是字符串比较)", !lt("10.0.0", "9.0.0"), "")
	check("CompareIdentifier 数字恒低于非数字",
		CompareIdentifier("1", "beta") == -1 && CompareIdentifier("beta", "1") == 1, "")
}

// ============================ 3. 递增规则 ============================

func testBump() {
	fmt.Println("[3] 递增规则")
	v := MustVersion("1.2.3-alpha.1+build.7")
	major, _ := v.Bump("major")
	minor, _ := MustVersion("1.2.3").Bump("minor")
	patch, _ := MustVersion("1.2.3").Bump("patch")
	pre, _ := MustVersion("1.2.3-rc.1").Bump("prerelease")
	check("major 递增清掉 minor/patch 与预发布", major.String() == "2.0.0", major.String())
	check("minor 递增把 patch 归零", minor.String() == "1.3.0", minor.String())
	check("patch 递增", patch.String() == "1.2.4", patch.String())
	check("官方例子: 1.9.0 -> 1.10.0",
		func() bool { m, _ := MustVersion("1.9.0").Bump("minor"); return m.String() == "1.10.0" }(), "")
	check("预发布递增(本 demo 约定)", pre.String() == "1.2.4-0", pre.String())
	if _, err := v.Bump("build"); err == nil {
		check("未知递增类型应报错", false, "")
	} else {
		check("未知递增类型应报错", true, "")
	}

	best, ok := Highest([]string{"1.2.0", "1.3.0-rc.1"}, true)
	check("Highest 默认含预发布", ok && best.String() == "1.3.0-rc.1", best.String())
	best, ok = Highest([]string{"1.2.0", "1.3.0-rc.1"}, false)
	check("Highest 排除预发布后取稳定版", ok && best.String() == "1.2.0", best.String())
	if _, ok := Highest([]string{"1.3.0-rc.1"}, false); ok {
		check("全是预发布且被排除时无结果", false, "")
	} else {
		check("全是预发布且被排除时无结果", true, "")
	}
}
