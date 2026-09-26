// provenance.go — 与 python/provenance.py 同语义的 Go 复刻(静态审查用)。
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
)

func Digest(data []byte) string {
	h := sha256.Sum256(data)
	return "sha256:" + hex.EncodeToString(h[:])
}

func Sign(key, payload []byte) []byte {
	m := hmac.New(sha256.New, key)
	m.Write(payload)
	return m.Sum(nil)
}

func Verify(key, payload, sig []byte) bool {
	return hmac.Equal(Sign(key, payload), sig)
}

// Statement:绑定 subject(摘要标识)与 predicateType。
type Statement struct {
	Type          string       `json:"_type"`
	Subject       []Subject    `json:"subject"`
	PredicateType string       `json:"predicateType"`
	Predicate     interface{}  `json:"predicate"`
}

type Subject struct {
	Name   string `json:"name"`
	Digest string `json:"digest"`
}

// VerifyPolicy:验签 → subject 摘要比对 → 谓词类型比对。
func VerifyPolicy(key []byte, stmt Statement, sig []byte, wantDigest, wantType string) (string, string) {
	payload, _ := json.Marshal(stmt)
	if !Verify(key, payload, sig) {
		return "reject", "签名无效"
	}
	if stmt.Subject[0].Digest != wantDigest {
		return "reject", "subject 摘要不匹配"
	}
	if stmt.PredicateType != wantType {
		return "reject", "谓词类型不匹配"
	}
	return "accept", "ok"
}

func main() {
	artifact := []byte("release-binary-v7")
	key := []byte("builder-secret")
	stmt := Statement{
		Type:          "https://in-toto.dev/Statement/v1",
		Subject:       []Subject{{"app-7.0.0.tar.gz", Digest(artifact)}},
		PredicateType: "https://slsa.dev/provenance/v1",
	}
	payload, _ := json.Marshal(stmt)
	sig := Sign(key, payload)
	fmt.Println(VerifyPolicy(key, stmt, sig, Digest(artifact), stmt.PredicateType)) // accept ok
	fmt.Println(VerifyPolicy([]byte("attacker"), stmt, sig, Digest(artifact), stmt.PredicateType))
}
