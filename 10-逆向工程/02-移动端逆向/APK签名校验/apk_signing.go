// apk_signing.go — APK 签名方案 v1/v2/v3 结构与校验(Go 版,与 apk_signing.py 同构)。
// 依据 source.android.com 官方规格; 签名用"键控摘要"替代真实 RSA/ECDSA(聚焦结构)。
// 本机无 go 工具链: 经人工审查 + 括号/引号配平脚本校验。
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"fmt"
)

var sigBlockMagic = []byte("APK Sig Block 42")

const (
	v2ID  = 0x7109871a
	v3ID  = 0xf05368c0
	porID = 0x3ba06f8c
	chunk = 1024 * 1024
)

func sha(b []byte) []byte { s := sha256.Sum256(b); return s[:] }

func keypair(seed []byte) ([]byte, []byte) {
	pub := sha(append([]byte("pub-"), seed...))
	return seed, pub
}
// sign = sha256("sig|"||priv||"|"||data); verify 以配对 pub 为准(见 verifySig)
func sign(priv, data []byte) []byte {
	buf := bytes.Join([][]byte{[]byte("sig|"), priv, []byte("|"), data}, nil)
	return sha(buf)
}
func verifySig(pub, priv []byte) bool {
	return bytes.Equal(sha(append([]byte("pub-"), priv...)), pub)
}
// ---------- v2 分块摘要(官方两级 Merkle 口径) ----------
func chunkDigests(data []byte) [][]byte {
	var out [][]byte
	for i := 0; i < len(data); i += chunk {
		end := i + chunk
		if end > len(data) {
			end = len(data)
		}
		c := data[i:end]
		pre := append([]byte{0xa5}, u32b(uint32(len(c)))...)
		pre = append(pre, c...)
		out = append(out, sha(pre))
	}
	return out
}
func topDigest(data []byte) []byte {
	chunks := chunkDigests(data)
	pre := append([]byte{0x5a}, u32b(uint32(len(chunks)))...)
	for _, c := range chunks {
		pre = append(pre, c...)
	}
	return sha(pre)
}
// apkDigest: 第 1/3/4 部分参与; EOCD(偏移 16 处 uint32)按签名分块偏移取值
func apkDigest(content, cd, eocd []byte, blockOffset uint32) []byte {
	e := make([]byte, len(eocd))
	copy(e, eocd)
	binary.LittleEndian.PutUint32(e[16:], blockOffset)
	all := append(append(append([]byte{}, content...), cd...), e...)
	return topDigest(all)
}
// ---------- 布局助手 ----------
func u32b(v uint32) []byte {
	var b [4]byte
	binary.LittleEndian.PutUint32(b[:], v)
	return b[:]
}
func u64b(v uint64) []byte {
	var b [8]byte
	binary.LittleEndian.PutUint64(b[:], v)
	return b[:]
}
func lp(data []byte) []byte { // uint32 长度前缀
	return append(u32b(uint32(len(data))), data...)
}
// ---------- APK Signing Block ----------
func buildSigningBlock(order []uint32, pairs map[uint32][]byte) []byte {
	var body []byte
	for _, id := range order {
		v := pairs[id]
		body = append(body, u64b(uint64(4+len(v)))...)
		body = append(body, u32b(id)...)
		body = append(body, v...)
	}
	size := uint64(8 + len(body) + 8)
	var blk []byte
	blk = append(blk, u64b(size)...)
	blk = append(blk, body...)
	blk = append(blk, u64b(size)...)
	blk = append(blk, sigBlockMagic...)
	return blk
}
func parseSigningBlock(block []byte) (map[uint32][]byte, error) {
	if len(block) < 24 {
		return nil, fmt.Errorf("block too short")
	}
	if !bytes.Equal(block[len(block)-16:], sigBlockMagic) {
		return nil, fmt.Errorf("magic mismatch")
	}
	size1 := binary.LittleEndian.Uint64(block[0:8])
	size2 := binary.LittleEndian.Uint64(block[len(block)-24 : len(block)-16])
	if size1 != size2 {
		return nil, fmt.Errorf("two size fields differ")
	}
	pairs := map[uint32][]byte{}
	off, end := 8, len(block)-24
	for off < end {
		plen := binary.LittleEndian.Uint64(block[off : off+8])
		off += 8
		id := binary.LittleEndian.Uint32(block[off : off+4])
		pairs[id] = block[off+4 : off+int(plen)]
		off += int(plen)
	}
	return pairs, nil
}
// ---------- v2/v3 signer ----------
func buildV2Signer(digest, pub, priv []byte) []byte {
	var sd []byte
	sd = append(sd, lp(append(u32b(0x0101), lp(digest)...))...)
	sd = append(sd, lp(append([]byte("CERT-"), pub...))...)
	sd = append(sd, u32b(0)...)
	sd = append(sd, u32b(0x7FFFFFFF)...)
	sig := sign(priv, sd)
	var signer []byte
	signer = append(signer, lp(sd)...)
	signer = append(signer, u32b(0)...)
	signer = append(signer, u32b(0x7FFFFFFF)...)
	signer = append(signer, lp(append(u32b(0x0101), lp(sig)...))...)
	signer = append(signer, lp(pub)...)
	return lp(signer)
}
func verifyV2Block(v2, content, cd, eocd []byte, blockOffset uint32, pub, priv []byte) (bool, string) {
	tot := binary.LittleEndian.Uint32(v2[0:4])
	if int(tot)+4 != len(v2) {
		return false, "signer length"
	}
	sdLen := binary.LittleEndian.Uint32(v2[4:8])
	sd := v2[8 : 8+sdLen]
	calc := apkDigest(content, cd, eocd, blockOffset)
	want := lp(append(u32b(0x0101), lp(calc)...))
	if !bytes.Contains(sd, want) {
		return false, "content digest mismatch"
	}
	if !bytes.Contains(v2, sign(priv, sd)) {
		return false, "signature mismatch"
	}
	if !bytes.Contains(v2, pub) {
		return false, "public key mismatch"
	}
	return true, "ok"
}
// ---------- v1 JAR 保护链 ----------
func buildV1(entries [][2][]byte) ([]byte, []byte) {
	var m bytes.Buffer
	for _, e := range entries {
		fmt.Fprintf(&m, "Name: %s\r\nSHA-256-Digest: %x\r\n\r\n", e[0], sha(e[1]))
	}
	mf := m.Bytes()
	sf := []byte(fmt.Sprintf("X-Android-APK-Signed: 2\r\n\r\nSHA-256-Digest-Manifest: %x\r\n\r\n", sha(mf)))
	return mf, sf
}
func verifyV1(entries [][2][]byte, mf, sf, sig, pub, priv []byte) (bool, string) {
	for _, e := range entries {
		want := fmt.Sprintf("Name: %s\r\nSHA-256-Digest: %x\r\n\r\n", e[0], sha(e[1]))
		if !bytes.Contains(mf, []byte(want)) {
			return false, "entry digest"
		}
	}
	want := fmt.Sprintf("SHA-256-Digest-Manifest: %x", sha(mf))
	if !bytes.Contains(sf, []byte(want)) {
		return false, "manifest digest"
	}
	if !bytes.Equal(sig, sign(priv, sf)) || !verifySig(pub, priv) {
		return false, "signature"
	}
	return true, "ok"
}
func rollbackProtected(sf []byte) bool {
	return bytes.Contains(sf, []byte("X-Android-APK-Signed: 2"))
}
// ---------- v3 proof-of-rotation ----------
// 证书 -> 私钥钥匙环(验证方视角; 真实实现用上一级证书的公钥验签)
var porPrivs = map[string][]byte{}

type porNode struct {
	certPub []byte
	certPriv []byte
	flags   uint32
}
func buildPOR(chain []porNode) []byte {
	var levels []byte
	for i, n := range chain {
		sd := append(lp(append([]byte("CERT-"), n.certPub...)), u32b(0x0101)...)
		var sig []byte
		if i > 0 { // 根节点无上一级
			sig = sign(chain[i-1].certPriv, sd)
		}
		node := append(lp(sd), u32b(n.flags)...)
		node = append(node, u32b(0x0101)...)
		node = append(node, lp(sig)...)
		levels = append(levels, lp(node)...)	}
	return lp(levels)
}
func verifyPOR(por, signerCert []byte) (bool, [][]byte) {
	lvLen := binary.LittleEndian.Uint32(por[0:4])
	off, end := 4, int(4+lvLen)
	if end > len(por) {
		end = len(por)
	}
	var certs [][]byte
	var prevPriv []byte
	ok := true
	for off < end {
		nLen := binary.LittleEndian.Uint32(por[off : off+4])
		off += 4
		node := por[off : off+nLen]
		off += int(nLen)
		sdLen := binary.LittleEndian.Uint32(node[0:4])
		sd := node[4 : 4+sdLen]
		certLen := binary.LittleEndian.Uint32(sd[0:4])
		cert := sd[4 : 4+certLen]
		certs = append(certs, cert)
		sigLP := 4 + sdLen + 8 // lp(sd)+flags+alg 之后是 lp(sig)
		sigLen := binary.LittleEndian.Uint32(node[sigLP : sigLP+4])
		sig := node[sigLP+4 : sigLP+4+sigLen]
		if prevPriv != nil && !bytes.Equal(sig, sign(prevPriv, sd)) {
			ok = false // 上一级证书没有为本级背书
		}
		prevPriv = porPrivs[string(cert)]
	}
	lastOK := len(certs) > 0 && bytes.Equal(certs[len(certs)-1], append([]byte("CERT-"), signerCert...))
	return ok && lastOK, certs
}
// ---------- 自检(与 Python 版断言同构) ----------
func check(label string, cond bool) {
	status := "PASS"
	if !cond {
		status = "FAIL"
	}
	fmt.Printf("%s - %s\n", status, label)
}
func main() {
	priv, pub := keypair([]byte("developer-key"))
	content := bytes.Repeat([]byte{0xAB}, 1024) // 小样本(口径同 Python 版 1MiB 场景)
	cd := []byte("CENTRAL-DIR")
	eocd := append(u32b(0x06054b50), make([]byte, 12)...) // 偏移 16 处为 CD 偏移字段的简化 EOCD
	eocd = append(eocd, u32b(0)...)
	eocd = append(eocd, u32b(0)...)

	blkOff := uint32(len(content))
	digest := apkDigest(content, cd, eocd, blkOff)
	v2 := buildV2Signer(digest, pub, priv)
	blk := buildSigningBlock([]uint32{v2ID}, map[uint32][]byte{v2ID: v2})
	pairs, err := parseSigningBlock(blk)
	check("1 布局解析(magic/双size/ID对)", err == nil && pairs[v2ID] != nil)
	ok, why := verifyV2Block(pairs[v2ID], content, cd, eocd, blkOff, pub, priv)
	check("2 v2 完整校验通过", ok && why == "ok")
	tampered := append([]byte{}, content...)
	tampered[100] ^= 0xFF
	ok2, _ := verifyV2Block(pairs[v2ID], tampered, cd, eocd, blkOff, pub, priv)
	check("3 单字节篡改 -> 拒绝", !ok2)

	entries := [][2][]byte{{[]byte("classes.dex"), []byte("DEX-BODY")}}
	mf, sf := buildV1(entries)
	sig := sign(priv, sf)
	ok3, _ := verifyV1(entries, mf, sf, sig, pub, priv)
	check("4 v1 保护链验证通过", ok3)
	check("5 防回滚属性在 .SF", rollbackProtected(sf))

	oldPriv, oldPub := keypair([]byte("old-key"))
	newPriv, newPub := keypair([]byte("new-key"))
	porPrivs[string(append([]byte("CERT-"), oldPub...))] = oldPriv
	porPrivs[string(append([]byte("CERT-"), newPub...))] = newPriv
	por := buildPOR([]porNode{{oldPub, oldPriv, 0}, {newPub, newPriv, 1}})
	okPor, certs := verifyPOR(por, newPub)
	check("6 合法 PoR 链通过", okPor && len(certs) == 2)
	fakePriv, fakePub := keypair([]byte("attacker"))
	porPrivs[string(append([]byte("CERT-"), fakePub...))] = fakePriv
	sdX := append(lp(append([]byte("CERT-"), fakePub...)), u32b(0x0101)...)
	node := append(lp(sdX), u32b(0)...)
	node = append(node, u32b(0x0101)...)
	node = append(node, lp(nil)...)
	fakePOR := lp(lp(node))
	okFake, _ := verifyPOR(fakePOR, newPub)
	check("7 攻击者自造根节点 -> 拒绝", !okFake)
	fmt.Println("ALL GO CHECKS DONE(需 go run 实跑复核)")
}
