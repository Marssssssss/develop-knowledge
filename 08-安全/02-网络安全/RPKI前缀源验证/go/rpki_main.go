// rpki_main.go —— RPKI 前缀源验证自检（Go，纯标准库）
//
// 运行：go run .
//
// 期望值来源：RFC 6482 §3/§3.3/§4、RFC 6811 §2/§2.1、RFC 3779（IPAddress 是 BIT STRING）。
package main

import (
	"bytes"
	"fmt"
	"net/netip"
)

var checks, failures int

func check(label string, ok bool, detail string) {
	checks++
	if !ok {
		failures++
		fmt.Printf("  FAIL %-46s %s\n", label, detail)
	}
}

func mustPrefix(s string) netip.Prefix {
	p, err := netip.ParsePrefix(s)
	if err != nil {
		panic(err)
	}
	return p
}

func state(p netip.Prefix, origin uint32, hasOrigin bool, vrps []vrp) pfState {
	return validate(vrps, p, origin, hasOrigin)
}

// RFC 6482 §3.3 的例子：203.0.113.0/24 + maxLength 26，AS 64496
func sampleVRPs() []vrp {
	r := roa{asn: 64496, addresses: []roaAddress{
		{prefix: mustPrefix("203.0.113.0/24"), maxLength: 26, hasMax: true},
	}}
	vrps, err := r.toVRPs()
	if err != nil {
		panic(err)
	}
	return vrps
}

func testPrefixOps() {
	p := mustPrefix("203.0.113.0/24")
	check("Bits", p.Bits() == 24, fmt.Sprint(p.Bits()))
	check("IPv4 识别", p.Addr().Is4(), "")
	check("Masked 清零主机位", mustPrefix("203.0.113.1/24").Masked().String() == "203.0.113.0/24",
		mustPrefix("203.0.113.1/24").Masked().String())
	check("跨族 Overlaps 为假", !p.Overlaps(mustPrefix("2001:db8::/32")), "")
	// Overlaps 两个方向都真，所以判定 Covered 必须自己加上长度条件
	inner := mustPrefix("203.0.113.128/25")
	check("Overlaps 是双向的", p.Overlaps(inner) && inner.Overlaps(p), "")
	check("Covered 要求 VRP 不更长", covered(vrp{p, 24, 1}, inner) && !covered(vrp{inner, 25, 1}, p), "")
}

func testBitString() {
	check("/24 未使用 0 位", bitStringContent(mustPrefix("203.0.113.0/24"))[0] == 0, "")
	got := bitStringContent(mustPrefix("203.0.113.0/24"))
	check("/24 内容 CB 00 71", bytes.Equal(got, []byte{0x00, 0xCB, 0x00, 0x71}),
		fmt.Sprintf("%x", got))
	got = bitStringContent(mustPrefix("203.0.113.0/26"))
	check("/26 未使用 6 位且左对齐", bytes.Equal(got, []byte{0x06, 0xCB, 0x00, 0x71, 0x00}),
		fmt.Sprintf("%x", got))
	got = bitStringContent(mustPrefix("203.0.113.192/26"))
	check("/26 上半个 C0", bytes.Equal(got, []byte{0x06, 0xCB, 0x00, 0x71, 0xC0}),
		fmt.Sprintf("%x", got))
	// 主机位必须被 Masked() 吃掉，否则不同写法会编出不同字节
	check("主机位不影响编码",
		bytes.Equal(bitStringContent(mustPrefix("203.0.113.1/24")),
			bitStringContent(mustPrefix("203.0.113.0/24"))), "")
	check("v6 的 /32 内容 16 字节内取前 4",
		len(bitStringContent(mustPrefix("2001:db8::/32"))) == 5, "")
}

func testRoaRules() {
	base := roa{asn: 64496, addresses: []roaAddress{
		{prefix: mustPrefix("203.0.113.0/24"), maxLength: 26, hasMax: true}}}
	check("合法 ROA 无问题", len(base.problems()) == 0, fmt.Sprint(base.problems()))

	noMax := roa{asn: 64496, addresses: []roaAddress{
		{prefix: mustPrefix("198.51.100.0/24")}}}
	vrps, err := noMax.toVRPs()
	check("缺省 maxLength = 前缀长度", err == nil && vrps[0].maxLength == 24, "")

	badVer := roa{version: 1, asn: 1, addresses: []roaAddress{
		{prefix: mustPrefix("10.0.0.0/8")}}}
	check("version != 0 被判非法", len(badVer.problems()) == 1, fmt.Sprint(badVer.problems()))
	if _, err := badVer.toVRPs(); err == nil {
		check("非法 ROA 必须拒绝展开", false, "")
	} else {
		check("非法 ROA 必须拒绝展开", true, "")
	}
	tooLong := roa{asn: 1, addresses: []roaAddress{
		{prefix: mustPrefix("10.0.0.0/8"), maxLength: 33, hasMax: true}}}
	tooShort := roa{asn: 1, addresses: []roaAddress{
		{prefix: mustPrefix("10.0.0.0/24"), maxLength: 16, hasMax: true}}}
	empty := roa{asn: 1}
	check("maxLength 越界/过短/空块都被判非法",
		len(tooLong.problems()) == 1 && len(tooShort.problems()) == 1 &&
			len(empty.problems()) == 1, "")
}

func testRfc6811() {
	vrps := sampleVRPs()
	for _, ok := range []string{"203.0.113.0/24", "203.0.113.0/25", "203.0.113.128/25",
		"203.0.113.0/26", "203.0.113.64/26", "203.0.113.192/26"} {
		check("Valid "+ok, state(mustPrefix(ok), 64496, true, vrps) == stateValid, ok)
	}
	for _, bad := range []string{"203.0.113.0/27", "203.0.113.64/27", "203.0.113.0/32"} {
		check("Invalid "+bad, state(mustPrefix(bad), 64496, true, vrps) == stateInvalid, bad)
	}
	for _, nf := range []string{"203.0.114.0/24", "203.0.112.0/24", "10.0.0.0/8"} {
		check("NotFound "+nf, state(mustPrefix(nf), 64496, true, vrps) == stateNotFound, nf)
	}
	check("源 AS 不符 Invalid",
		state(mustPrefix("203.0.113.0/24"), 65000, true, vrps) == stateInvalid, "")
	check("源 AS 为 NONE Invalid",
		state(mustPrefix("203.0.113.0/24"), 0, false, vrps) == stateInvalid, "")
	check("空 VRP 库全 NotFound",
		state(mustPrefix("203.0.113.0/24"), 64496, true, nil) == stateNotFound, "")

	zero, err := roa{asn: 0, addresses: []roaAddress{
		{prefix: mustPrefix("203.0.113.0/24"), maxLength: 26, hasMax: true}}}.toVRPs()
	check("AS 0 的 VRP 永不 Matched",
		err == nil && state(mustPrefix("203.0.113.0/24"), 0, true, zero) == stateInvalid, "")

	exact, err := roa{asn: 64496, addresses: []roaAddress{
		{prefix: mustPrefix("198.51.100.0/24")}}}.toVRPs()
	check("缺省 maxLength 只授权精确前缀",
		err == nil && state(mustPrefix("198.51.100.0/24"), 64496, true, exact) == stateValid &&
			state(mustPrefix("198.51.100.0/25"), 64496, true, exact) == stateInvalid, "")

	multi, err := roa{asn: 64496, addresses: []roaAddress{
		{prefix: mustPrefix("203.0.113.0/24"), maxLength: 26, hasMax: true},
		{prefix: mustPrefix("203.0.113.0/28"), maxLength: 28, hasMax: true}}}.toVRPs()
	check("一条 ROA 内前缀互相包含也确定",
		err == nil && len(multi) == 2 &&
			state(mustPrefix("203.0.113.0/28"), 64496, true, multi) == stateValid &&
			state(mustPrefix("203.0.113.0/27"), 64496, true, multi) == stateInvalid, "")

	v6, err := roa{asn: 64496, addresses: []roaAddress{
		{prefix: mustPrefix("2001:db8::/32"), maxLength: 48, hasMax: true}}}.toVRPs()
	check("IPv6 的 maxLength",
		err == nil && state(mustPrefix("2001:db8:1::/48"), 64496, true, v6) == stateValid &&
			state(mustPrefix("2001:db8:1::/49"), 64496, true, v6) == stateInvalid &&
			state(mustPrefix("2001:db9::/32"), 64496, true, v6) == stateNotFound, "")
}

func testOriginASN() {
	local := uint32(65000)
	got, ok := originASN([]asPathSegment{{asSequence, []uint32{65001, 65002}}}, local)
	check("AS_SEQUENCE 取最右 AS", ok && got == 65002, fmt.Sprint(got))
	got, ok = originASN(nil, local)
	check("空 AS_PATH 取本机 AS", ok && got == local, fmt.Sprint(got))
	got, ok = originASN([]asPathSegment{{asConfedSequence, []uint32{64512}}}, local)
	check("联邦段取本机 AS", ok && got == local, fmt.Sprint(got))
	_, ok = originASN([]asPathSegment{{asSequence, []uint32{65001}}, {asSet, []uint32{2, 3}}}, local)
	check("末段 AS_SET → NONE", !ok, "")
	got, ok = originASN([]asPathSegment{{asSequence, []uint32{4200000000}}}, local)
	check("4 字节 AS", ok && got == 4200000000, fmt.Sprint(got))
}

func testOID() {
	// 注意局部变量名不要叫 roa，否则会遮蔽同名的结构体类型
	roaOID := encodeOID([]uint32{1, 2, 840, 113549, 1, 9, 16, 1, 24})
	check("ROA 内容类型 OID", bytes.Equal(roaOID, []byte{0x2a, 0x86, 0x48, 0x86, 0xf7,
		0x0d, 0x01, 0x09, 0x10, 0x01, 0x18}), fmt.Sprintf("%x", roaOID))
	rsa := encodeOID([]uint32{1, 2, 840, 113549, 1, 1, 11})
	check("sha256WithRSAEncryption OID", bytes.Equal(rsa, []byte{0x2a, 0x86, 0x48, 0x86,
		0xf7, 0x0d, 0x01, 0x01, 0x0b}), fmt.Sprintf("%x", rsa))
}

func main() {
	groups := []struct {
		name string
		fn   func()
	}{
		{"netip 前缀运算", testPrefixOps},
		{"BIT STRING 左对齐", testBitString},
		{"ROA 合法性约束", testRoaRules},
		{"RFC 6811 三态验证", testRfc6811},
		{"AS_PATH → 源 AS", testOriginASN},
		{"OID 的 DER 编码", testOID},
	}
	for _, g := range groups {
		before, badBefore := checks, failures
		g.fn()
		mark := ""
		if failures != badBefore {
			mark = "  <-- FAIL"
		}
		fmt.Printf("  %-24s %d checks%s\n", g.name, checks-before, mark)
	}
	fmt.Printf("rpki_main: %d checks passed, %d failed\n", checks-failures, failures)
}
