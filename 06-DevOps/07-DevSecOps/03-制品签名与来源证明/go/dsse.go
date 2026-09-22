// Package sigstore 的 DSSE / cosign / bundle 部分，是 python/main.py 的 Go 转写。
package sigstore

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math/big"
	"strconv"
	"strings"
)

// bytesToInt 把大端字节串解释成整数（raw r||s 编码用）。
func bytesToInt(b []byte) *big.Int { return new(big.Int).SetBytes(b) }

// ---------------------------------------------------------------- PAE

// PAE 是 DSSE 的 Pre-Authentication Encoding：
//
//	PAE(type, body) = "DSSEv1" SP LEN(type) SP type SP LEN(body) SP body
//
// LEN 是**字节长度**的十进制（无前导零），type 先做 UTF-8 编码。
func PAE(payloadType string, body []byte) []byte {
	t := []byte(payloadType)
	var sb strings.Builder
	sb.WriteString("DSSEv1")
	sb.WriteString(" ")
	sb.WriteString(strconv.Itoa(len(t)))
	sb.WriteString(" ")
	sb.Write(t)
	sb.WriteString(" ")
	sb.WriteString(strconv.Itoa(len(body)))
	sb.WriteString(" ")
	sb.Write(body)
	return []byte(sb.String())
}

// ---------------------------------------------------------------- 信封

// Envelope 是 DSSE 的 JSON 信封。
type Envelope struct {
	Payload     string       `json:"payload"`
	PayloadType string       `json:"payloadType"`
	Signatures  []Signature  `json:"signatures"`
	Extra       map[string]any `json:"-"`
}

// Signature 是信封里的一项签名；KeyID 可选且未认证。
type Signature struct {
	KeyID string `json:"keyid,omitempty"`
	Sig   string `json:"sig"`
}

// KeyIDOf 把「未设置」与「空串」同等对待。
func KeyIDOf(s Signature) string { return s.KeyID }

// DecodeSig 把 base64 的 sig 解成 (r, s)；编码是原始 r||s，不是 DER。
func DecodeSig(sigB64 string) (*big.Int, *big.Int, error) {
	raw, err := base64.StdEncoding.DecodeString(sigB64)
	if err != nil {
		return nil, nil, err
	}
	if len(raw) != 64 {
		return nil, nil, fmt.Errorf("signature must be 64 bytes (raw r||s), got %d", len(raw))
	}
	return bytesToInt(raw[:32]), bytesToInt(raw[32:]), nil
}

// VerifyEnvelope 用给定公钥验签 DSSE 信封。
func VerifyEnvelope(pub Point, env Envelope) bool {
	payload, err := base64.StdEncoding.DecodeString(env.Payload)
	if err != nil {
		return false
	}
	if len(env.Signatures) == 0 {
		return false
	}
	msg := PAE(env.PayloadType, payload)
	for _, s := range env.Signatures {
		r, sv, err := DecodeSig(s.Sig)
		if err != nil {
			continue
		}
		if Verify(pub, msg, r, sv) {
			return true
		}
	}
	return false
}

// VerifyThreshold 是 (t, n) 多签：需要至少 t 个**互不相同**的受信任公钥。
func VerifyThreshold(pubs map[string]Point, env Envelope, t int) bool {
	payload, err := base64.StdEncoding.DecodeString(env.Payload)
	if err != nil {
		return false
	}
	msg := PAE(env.PayloadType, payload)
	accepted := map[string]bool{}
	for _, s := range env.Signatures {
		r, sv, err := DecodeSig(s.Sig)
		if err != nil {
			continue
		}
		for name, pub := range pubs {
			if accepted[name] {
				continue
			}
			if Verify(pub, msg, r, sv) {
				accepted[name] = true
				break
			}
		}
	}
	return len(accepted) >= t
}

// ---------------------------------------------------------------- cosign

// cosign 的常量（specs/SIGNATURE_SPEC.md）。
const (
	SigAnnotation   = "dev.cosignproject.cosign/signature"
	CertAnnotation  = "dev.cosignproject.cosign/certificate"
	ChainAnnotation = "dev.cosignproject.cosign/chain"
	SimpleMediaType = "application/vnd.dev.cosign.simplesigning.v1+json"
	SimpleType      = "cosign container image signature"
)

// SigTagForDigest 是 tag-based discovery：':' 换成 '-' 再加 ".sig" 后缀。
func SigTagForDigest(digest string) (string, error) {
	if !strings.Contains(digest, ":") {
		return "", fmt.Errorf("digest must be of the form <alg>:<hex>")
	}
	return strings.ReplaceAll(digest, ":", "-") + ".sig", nil
}

// SimpleSigningPayload 构造 Simple Signing 载荷。
func SimpleSigningPayload(dockerRef, manifestDigest string, optional map[string]any) []byte {
	if optional == nil {
		optional = map[string]any{}
	}
	obj := map[string]any{
		"critical": map[string]any{
			"identity": map[string]any{"docker-reference": dockerRef},
			"image":    map[string]any{"Docker-manifest-digest": manifestDigest},
			"type":     SimpleType,
		},
		"optional": optional,
	}
	b, _ := json.Marshal(obj)
	return b
}

// SHA256Hex 返回字节串的 sha256 十六进制。
func SHA256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return fmt.Sprintf("%x", sum[:])
}

// ---------------------------------------------------------------- bundle

// bundle 的 media type（sigstore_bundle.proto）。
const BundleMediaTypeCurrent = "application/vnd.dev.sigstore.bundle.v0.3+json"

var bundleLegacy = []string{
	"application/vnd.dev.sigstore.bundle+json;version=0.1",
	"application/vnd.dev.sigstore.bundle+json;version=0.2",
	"application/vnd.dev.sigstore.bundle+json;version=0.3",
}

// AcceptBundleMediaType 判定 media type 是否可接受。
// 口径说明：规范只说客户端必须接受此前定义的格式，未规定未知版本，此处取「拒绝」。
func AcceptBundleMediaType(mt string) bool {
	if mt == BundleMediaTypeCurrent {
		return true
	}
	for _, old := range bundleLegacy {
		if mt == old {
			return true
		}
	}
	return false
}

// Bundle 是 Sigstore bundle 的最小子集。
type Bundle struct {
	MediaType            string         `json:"mediaType"`
	VerificationMaterial map[string]any `json:"verificationMaterial,omitempty"`
	DsseEnvelope         *Envelope      `json:"dsseEnvelope,omitempty"`
}

// ValidateBundle 返回错误列表，空表示通过。
func ValidateBundle(b Bundle) []string {
	var errs []string
	if !AcceptBundleMediaType(b.MediaType) {
		errs = append(errs, "mediaType 不是可接受的 bundle 类型")
	}
	if b.VerificationMaterial == nil {
		errs = append(errs, "verificationMaterial 是 REQUIRED")
	}
	if b.DsseEnvelope != nil {
		// bundle 内的 DSSE envelope 必须恰好一个签名
		n := len(b.DsseEnvelope.Signatures)
		if n != 1 {
			errs = append(errs, fmt.Sprintf("bundle 内的 DSSE envelope 必须恰好一个签名, 实际 %d", n))
		} else if hint, ok := b.VerificationMaterial["publicKey"]; ok {
			if m, ok2 := hint.(map[string]any); ok2 {
				if h, ok3 := m["hint"]; ok3 {
					if hs, ok4 := h.(string); ok4 && hs != KeyIDOf(b.DsseEnvelope.Signatures[0]) {
						errs = append(errs, "key hint 在 verificationMaterial 与 DSSE envelope 中不一致")
					}
				}
			}
		}
	}
	return errs
}

// TrustIntegratedTime 对应 rekor.proto：缺 inclusion_promise 时不得信任时间。
func TrustIntegratedTime(entry map[string]any) bool {
	_, ok := entry["inclusionPromise"]
	return ok
}
