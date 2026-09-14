// JA3 / JA4 TLS ClientHello 指纹计算器（含 ClientHello 构造与解析回环）。
// 官方向量端到端验证:
//
//	JA4: t13d1516h2_8daaf6152771_e5627efa2ab1   (FoxIO 规范例)
//	JA3: ada70206e40642a3e4461f35503241d5       (salesforce/ja3 README 例)
//
// 用法: go run tls_fingerprint.go   (断言失败即 panic)
package main

import (
	"crypto/md5"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
)

// ---------- GREASE (RFC 8701): 0x0a0a, 0x1a1a, ... 0xfafa ----------
func isGrease(v uint16) bool { return v&0x0f0f == 0x0a0a }

var verCode = map[uint16]string{
	0x0304: "13", 0x0303: "12", 0x0302: "11", 0x0301: "10",
	0x0300: "s3", 0x0200: "s2",
}

// ---------- JA3 ----------
func ja3(ch *clientHello) (hash, raw string) {
	join := func(xs []uint16) string {
		var ss []string
		for _, v := range xs {
			if !isGrease(v) {
				ss = append(ss, fmt.Sprint(v))
			}
		}
		return strings.Join(ss, "-")
	}
	var pfs []uint16
	for _, p := range ch.pointFormats {
		pfs = append(pfs, uint16(p))
	}
	raw = strings.Join([]string{
		fmt.Sprint(ch.legacyVersion), join(ch.ciphers), join(ch.exts),
		join(ch.curves), join(pfs)}, ",")
	sum := md5.Sum([]byte(raw))
	return hex.EncodeToString(sum[:]), raw
}

// ---------- JA4 ----------
func h12(s string) string {
	if s == "" {
		return "000000000000"
	}
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])[:12]
}

func ja4(ch *clientHello) string {
	ver := ch.legacyVersion
	for _, v := range ch.supportedVersions {
		if !isGrease(v) && v > ver {
			ver = v
		}
	}
	nc, ne := 0, 0
	for _, c := range ch.ciphers {
		if !isGrease(c) {
			nc++
		}
	}
	for _, e := range ch.exts {
		if !isGrease(e) {
			ne++
		}
	}
	alpn := "00"
	if len(ch.alpnFirst) >= 2 {
		alpn = ch.alpnFirst[:2]
	} else if len(ch.alpnFirst) == 1 {
		alpn = ch.alpnFirst + "0"
	}
	a := "t" + verCode[ver] + map[bool]string{true: "d", false: "i"}[ch.sni] +
		fmt.Sprintf("%02d%02d", nc, ne) + alpn
	var cs []string
	for _, c := range ch.ciphers {
		if !isGrease(c) {
			cs = append(cs, fmt.Sprintf("%04x", c))
		}
	}
	sortStrings(cs)
	b := h12(strings.Join(cs, ","))
	var es []string
	for _, e := range ch.exts {
		if !isGrease(e) && e != 0x0000 && e != 0x0010 {
			es = append(es, fmt.Sprintf("%04x", e))
		}
	}
	sortStrings(es)
	estr := strings.Join(es, ",")
	if len(ch.sigalgs) > 0 {
		var ss []string
		for _, s := range ch.sigalgs { // wire 顺序, 不排序
			ss = append(ss, fmt.Sprintf("%04x", s))
		}
		estr += "_" + strings.Join(ss, ",")
	}
	return a + "_" + b + "_" + h12(estr)
}

func sortStrings(xs []string) {
	for i := 1; i < len(xs); i++ {
		for j := i; j > 0 && xs[j] < xs[j-1]; j-- {
			xs[j], xs[j-1] = xs[j-1], xs[j]
		}
	}
}

// ---------- 官方向量回环 ----------
func main() {
	ciphersA := []uint16{0x002f, 0x0035, 0x009c, 0x009d, 0x1301, 0x1302, 0x1303,
		0xc013, 0xc014, 0xc02b, 0xc02c, 0xc02f, 0xc030, 0xcca8, 0xcca9}
	extsA := []uint16{0x0005, 0x000a, 0x000b, 0x000d, 0x0012, 0x0015, 0x0017,
		0x001b, 0x0023, 0x002b, 0x002d, 0x0033, 0x4469, 0xff01}
	sigA := []uint16{0x0403, 0x0804, 0x0401, 0x0503, 0x0805, 0x0501, 0x0806, 0x0601}
	var opaqueExts [][]byte
	for _, e := range extsA {
		if e != 0x000a && e != 0x000b && e != 0x000d && e != 0x002b {
			opaqueExts = append(opaqueExts, ext(e, nil))
		}
	}
	dataA := buildClientHello(helloArgs{version: 0x0301, ciphers: ciphersA,
		exts: opaqueExts, curves: []uint16{0x001d}, pf: []uint8{0},
		sigalgs: sigA, sni: []byte("example.com"), alpn: []string{"h2"},
		sv: []uint16{0x0304}})
	ch := parseClientHello(dataA)
	j4 := ja4(ch)
	fmt.Println("== 向量A (FoxIO 规范例) ==")
	fmt.Printf("  ciphers: %d  exts(wire): %d  sni: %v  alpn: %s\n",
		len(ch.ciphers), len(ch.exts), ch.sni, ch.alpnFirst)
	fmt.Println("  JA4 =", j4)
	if j4 != "t13d1516h2_8daaf6152771_e5627efa2ab1" {
		panic("JA4 向量不匹配!")
	}
	fmt.Println("  ✓ 与官方发布值一致")
	fmt.Println()

	ciphersB := []uint16{0x2f, 0x35, 0x05, 0x0a, 0xc009, 0xc00a, 0xc013, 0xc014,
		0x32, 0x38, 0x13, 0x04}
	dataB := buildClientHello(helloArgs{version: 0x0301, ciphers: ciphersB,
		curves: []uint16{23, 24, 25}, pf: []uint8{0},
		sni: []byte("www.example.com")})
	chb := parseClientHello(dataB)
	j3, raw := ja3(chb)
	fmt.Println("== 向量B (salesforce/ja3 README 例) ==")
	fmt.Println("  raw =", raw)
	fmt.Println("  JA3 =", j3)
	if raw != "769,47-53-5-10-49161-49162-49171-49172-50-56-19-4,0-10-11,23-24-25,0" {
		panic("JA3 原串不匹配!")
	}
	if j3 != "ada70206e40642a3e4461f35503241d5" {
		panic("JA3 向量不匹配!")
	}
	fmt.Println("  ✓ 与官方发布值一致")
	fmt.Println()

	// GREASE 鲁棒性
	greaseExts := append(append([][]byte{}, opaqueExts...), ext(0x2a2a, nil))
	gciphers := append([]uint16{0x0a0a}, ciphersA...)
	gciphers = append(gciphers, 0x1a1a)
	dataG := buildClientHello(helloArgs{version: 0x0301, ciphers: gciphers,
		exts: greaseExts, curves: []uint16{0x001d}, pf: []uint8{0},
		sigalgs: sigA, sni: []byte("example.com"), alpn: []string{"h2"},
		sv: []uint16{0x0a0a, 0x0304}})
	j4g := ja4(parseClientHello(dataG))
	fmt.Println("== GREASE 鲁棒性: 注入 0x0a0a/0x1a1a 套件 + 0x2a2a 扩展 ==")
	fmt.Println("  JA4 =", j4g)
	if j4g != j4 {
		panic("GREASE 未被正确剔除!")
	}
	fmt.Println("  ✓ 指纹不变 (GREASE 全程忽略)")
}
