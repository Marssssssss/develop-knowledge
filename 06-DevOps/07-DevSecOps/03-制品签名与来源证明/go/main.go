package main

// main.go 把 python/selfcheck_dsse.py 的关键结论在 Go 侧跑一遍：
// 官方 DSSE 测试向量的 PAE、ECDSA 验签、RFC6979 签名复现、cosign tag 映射。

import (
	"encoding/base64"
	"fmt"
	"math/big"

	s "sigstore"
)

// DSSE protocol.md 的官方测试向量。
const (
	vecPayloadType = "http://example.com/HelloWorld"
	vecPayload     = "hello world"
	vecSigB64      = "A3JqsQGtVsJ2O2xqrI5IcnXip5GToJ3F+FnZ+O88SjtR6rDAajabZKciJTfUiHqJPcIAriEGAHTVeCUjW2JIZA=="
	vecX           = "46950820868899156662930047687818585632848591499744589407958293238635476079160"
	vecY           = "5640078356564379163099075877009565129882514886557779369047442380624545832820"
	vecD           = "97358161215184420915383655311931858321456579547487070936769975997791359926199"
)

func dec(v string) *big.Int {
	x, ok := new(big.Int).SetString(v, 10)
	if !ok {
		panic("bad decimal: " + v)
	}
	return x
}

func intTo32(x *big.Int) []byte {
	out := make([]byte, 32)
	x.FillBytes(out)
	return out
}

func main() {
	// A1：PAE 应与官方向量逐字节一致
	fmt.Printf("A1 PAE: %q\n", string(s.PAE(vecPayloadType, []byte(vecPayload))))

	d := dec(vecD)
	x, y := dec(vecX), dec(vecY)
	pub := s.Point{X: x, Y: y}

	// B0：d·G 应等于官方公钥
	derived := s.Mul(s.G(), d)
	fmt.Println("B0 d*G == official (X,Y):", derived.X.Cmp(x) == 0 && derived.Y.Cmp(y) == 0)

	// B1/B2：官方签名验签 / 篡改后失败
	r, sv, err := s.DecodeSig(vecSigB64)
	if err != nil {
		panic(err)
	}
	msg := s.PAE(vecPayloadType, []byte(vecPayload))
	fmt.Println("B1 verify official sig:", s.Verify(pub, msg, r, sv))
	fmt.Println("B2 verify tampered payload:",
		s.Verify(pub, s.PAE(vecPayloadType, []byte("hello world!")), r, sv))

	// C1：RFC6979 签名应逐字节复现官方向量
	r2, s2 := s.Sign(d, msg)
	got := base64.StdEncoding.EncodeToString(append(intTo32(r2), intTo32(s2)...))
	fmt.Println("C1 reproduce official sig:", got == vecSigB64)

	// F1：cosign tag 映射
	tag, _ := s.SigTagForDigest("sha256:97fc222cee7991b5b061d4d4afdb5f3428fcb0c9054e1690313786befa1e4e36")
	fmt.Println("F1 sig tag:", tag)

	// G2：bundle 内的 DSSE envelope 必须恰好一个签名
	env := s.Envelope{
		Payload:     base64.StdEncoding.EncodeToString([]byte(vecPayload)),
		PayloadType: vecPayloadType,
		Signatures:  []s.Signature{{Sig: vecSigB64}},
	}
	b := s.Bundle{MediaType: s.BundleMediaTypeCurrent,
		VerificationMaterial: map[string]any{}, DsseEnvelope: &env}
	fmt.Println("G2 single sig ok:", len(s.ValidateBundle(b)) == 0)

	two := env
	two.Signatures = []s.Signature{{Sig: vecSigB64}, {Sig: vecSigB64}}
	b2 := s.Bundle{MediaType: s.BundleMediaTypeCurrent,
		VerificationMaterial: map[string]any{}, DsseEnvelope: &two}
	fmt.Println("G2c two sigs rejected:", len(s.ValidateBundle(b2)) > 0)

	// G5：integrated_time 的可信性
	fmt.Println("G5 with promise:", s.TrustIntegratedTime(map[string]any{"inclusionPromise": 1}))
	fmt.Println("G5b without promise:", s.TrustIntegratedTime(map[string]any{"integratedTime": 1}))
}
