// ech_check.go —— 自检公共设施与协议夹具
//
// 夹具里的服务器密钥对固定取自 RFC 9180 附录 A.2 的 ikmR/skRm/pkRm：ECH 端到端用例
// 与 HPKE 官方向量因此共用同一把密钥，任何一层（suite_id 长度、"HPKE-v1" 前缀、
// 三个 LabeledExtract 的盐、nonce 的 XOR 位置）错位都会同时把两组用例打红。
package main

import (
	"bytes"
	"encoding/hex"
	"fmt"
)

var (
	checksTotal  int
	checksFailed int
)

// check —— 仓库约定的三参形式：label / 条件 / 失败时要打印的补充信息
func check(label string, ok bool, detail string) {
	checksTotal++
	if !ok {
		checksFailed++
		fmt.Printf("  [FAIL] %s | %s\n", label, detail)
	}
}

func mustHex(s string) []byte {
	b, err := hex.DecodeString(s)
	if err != nil {
		panic("自检向量不是合法十六进制: " + s)
	}
	return b
}

func eqHex(got []byte, want string) bool { return bytes.Equal(got, mustHex(want)) }

func hexOf(b []byte) string { return hex.EncodeToString(b) }

func fill(b []byte, v byte) {
	for i := range b {
		b[i] = v
	}
}

// ---------------------------------------------------------------- 协议夹具

const (
	testConfigID = 0x7F
	testMaxName  = 64
	testPublic   = "example.com"
	testSNI      = "secret.example.org"
)

// serverNameExt —— extension_data = ServerNameList<1..2^16-1>
//
//	u16(3 + len(name)) | name_type(0) | u16(len(name)) | name
func serverNameExt(name string) []byte {
	out := []byte{byte((len(name) + 3) >> 8), byte(len(name) + 3), 0x00,
		byte(len(name) >> 8), byte(len(name))}
	return append(out, name...)
}

// keyShareExt —— group(0x001d) + 32 字节假公钥；两端逐字节相同才可被压缩
func keyShareExt() []byte {
	b := make([]byte, 34)
	fill(b, 0xAB)
	b[0], b[1] = 0x00, 0x1D
	return b
}

func commonCH(random, sid []byte, sni string, withEch bool) *clientHello {
	ch := &clientHello{
		Random:       append([]byte(nil), random...),
		SessionID:    append([]byte(nil), sid...),
		CipherSuites: []uint16{0x1301, 0x1302, 0x1303},
		Compression:  []byte{0x00},
	}
	ch.setExt(extServerName, serverNameExt(sni))
	ch.setExt(extSupportedVersion, []byte{0x02, 0x03, 0x04})
	ch.setExt(extKeyShare, keyShareExt())
	if withEch {
		ch.setExt(echExtType, nil) // inner 变体是 Empty
	}
	return ch
}

func makeInner(random, sid []byte) *clientHello {
	return commonCH(random, sid, testSNI, true)
}

func makeOuterTpl(random, sid []byte) *clientHello {
	return commonCH(random, sid, testPublic, false)
}

func makeConfig(configID byte, pk []byte) *echConfig {
	return &echConfig{
		KeyConfig: hpkeKeyConfig{
			ConfigID:     configID,
			KemID:        kemIDX25519,
			PublicKey:    append([]byte(nil), pk...),
			CipherSuites: [][2]uint16{{kdfIDHKDFSHA256, aeadIDChaCha20Poly1305}},
		},
		MaxNameLength: testMaxName,
		PublicName:    []byte(testPublic),
		Version:       echVersion,
	}
}

func sameCH(a, b *clientHello) bool {
	if !bytes.Equal(a.Random, b.Random) || !bytes.Equal(a.SessionID, b.SessionID) ||
		!bytes.Equal(a.Compression, b.Compression) || len(a.Exts) != len(b.Exts) ||
		len(a.CipherSuites) != len(b.CipherSuites) {
		return false
	}
	for i := range a.CipherSuites {
		if a.CipherSuites[i] != b.CipherSuites[i] {
			return false
		}
	}
	for i := range a.Exts {
		if a.Exts[i].Type != b.Exts[i].Type || !bytes.Equal(a.Exts[i].Data, b.Exts[i].Data) {
			return false
		}
	}
	return true
}

func hasSub(haystack []byte, needle string) bool {
	return bytes.Contains(haystack, []byte(needle))
}

// rebuildEchExt —— 保持 (cipher_suite, config_id, enc) 不变，只换 payload
func rebuildEchExt(ch *clientHello, payload []byte) error {
	o, err := decodeOuterExt(ch.Ext(echExtType))
	if err != nil {
		return err
	}
	o.Payload = payload
	return ch.setOuterExt(o)
}

// encryptRaw —— §6.1.1 的流程，但明文由调用方给定（用来构造恶意的
// EncodedClientHelloInner，例如尾部填充非零）。不做 32 字节对齐，所以服务端的
// 「填充必须全零」检查才会真正被触发。
func encryptRaw(outerTpl *clientHello, cfg *echConfig, plaintext []byte) (*clientHello, error) {
	enc, ctx, err := setupBaseS(cfg.KeyConfig.PublicKey, nil, nil)
	if err != nil {
		return nil, err
	}
	zero := outerTpl.copy()
	if err := zero.setOuterExt(newOuterExt(cfg, enc,
		make([]byte, len(plaintext)+aeadTagLen))); err != nil {
		return nil, err
	}
	aad, err := zero.Encode()
	if err != nil {
		return nil, err
	}
	sealed, err := ctx.Seal(aad, plaintext)
	if err != nil {
		return nil, err
	}
	out := outerTpl.copy()
	if err := out.setOuterExt(newOuterExt(cfg, enc, sealed)); err != nil {
		return nil, err
	}
	return out, nil
}
