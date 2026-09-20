package main

import (
	"crypto/sha256"
	"encoding/json"
	"strings"
)

// ATHOf 实现 RFC 9449 §4.2 的 ath。
func ATHOf(accessToken string) string {
	sum := sha256.Sum256([]byte(accessToken))
	return b64u(sum[:])
}

// StripQueryFragment 实现 §4.3(9)：比较 htu 时忽略 query 与 fragment。
func StripQueryFragment(uri string) string {
	if i := strings.IndexAny(uri, "?#"); i >= 0 {
		return uri[:i]
	}
	return uri
}

// Request 建模一次 HTTP 请求。
type Request struct {
	Method       string
	URI          string
	DPoPHeaders  []string
	Authorization string
	AccessToken  string
}

// DPoPServer 是 AS / RS 共用的 DPoP 校验器。
type DPoPServer struct {
	Name          string
	RequireNonce  bool
	MaxAge        int64
	Now           int64
	RecentNonces  []string
	seenJTI       map[string]bool
	nonceCounter  int
}

// NewServer 构造校验器。
func NewServer(name string, requireNonce bool, now int64) *DPoPServer {
	return &DPoPServer{Name: name, RequireNonce: requireNonce, MaxAge: 60,
		Now: now, seenJTI: map[string]bool{}}
}

// IssueNonce 下发一个新 nonce 并保留近期窗口。
func (s *DPoPServer) IssueNonce() string {
	s.nonceCounter++
	sum := sha256.Sum256([]byte(s.Name + string(rune('0'+s.nonceCounter))))
	n := "N-" + b64u(sum[:])[:12]
	s.RecentNonces = append(s.RecentNonces, n)
	if len(s.RecentNonces) > 4 {
		s.RecentNonces = s.RecentNonces[1:]
	}
	return n
}

// CheckProof 执行 RFC 9449 §4.3 的 12 条校验，返回 (通过, 原因, payload)。
func (s *DPoPServer) CheckProof(r Request, expectedToken, expectedJkt string) (bool, string, map[string]interface{}) {
	if len(r.DPoPHeaders) != 1 {
		return false, "E1 DPoP 头不是恰好一个", nil
	}
	okv, whyv, header, payload := JWSVerify(r.DPoPHeaders[0])
	if !okv {
		return false, "E2/E6 " + whyv, nil
	}
	for _, c := range []string{"jti", "htm", "htu", "iat"} {
		if _, has := payload[c]; !has {
			return false, "E3 缺 " + c, payload
		}
	}
	if header["typ"] != "dpop+jwt" {
		return false, "E4 typ 不是 dpop+jwt", payload
	}
	alg := header["alg"]
	if !AsymAlgs[alg] || alg == "none" || alg == "HS256" {
		return false, "E5 alg 不是可接受的非对称算法", payload
	}
	var jwk map[string]string
	if raw, has := header["jwk"]; has {
		json.Unmarshal([]byte(raw), &jwk)
	}
	for _, priv := range []string{"d", "p", "q", "k"} {
		if _, has := jwk[priv]; has {
			return false, "E7 jwk 含私钥成员", payload
		}
	}
	if s.RequireNonce {
		got, has := payload["nonce"].(string)
		if !has {
			return false, "E10 缺 nonce", payload
		}
		found := false
		for _, n := range s.RecentNonces {
			if n == got {
				found = true
			}
		}
		if !found {
			return false, "E10 nonce 不是服务器近期下发的值", payload
		}
	}
	if payload["htm"] != r.Method {
		return false, "E8 htm 不匹配", payload
	}
	htu, _ := payload["htu"].(string)
	if StripQueryFragment(htu) != StripQueryFragment(r.URI) {
		return false, "E9 htu 不匹配", payload
	}
	iat := int64(payload["iat"].(float64))
	if s.Now-iat > s.MaxAge || iat-s.Now > s.MaxAge {
		return false, "E11 iat 超出可接受窗口", payload
	}
	if expectedToken != "" {
		if payload["ath"] != ATHOf(expectedToken) {
			return false, "E12a ath 不等于该访问令牌的哈希", payload
		}
		if expectedJkt != "" && JWKThumbprint(jwk) != expectedJkt {
			return false, "E12b 证明公钥与令牌绑定的 jkt 不一致", payload
		}
	}
	return true, "ok", payload
}

// SingleUse 实现 §11.1 的 jti 单用检查。
func (s *DPoPServer) SingleUse(jti string) bool {
	if s.seenJTI[jti] {
		return false
	}
	s.seenJTI[jti] = true
	return true
}

// X5TS256 实现 RFC 8705 §3.1 的证书指纹（无尾随 '='）。
func X5TS256(der []byte) string {
	sum := sha256.Sum256(der)
	return b64u(sum[:])
}

// MTLSResourceCheck 对比 TLS 层证书与令牌绑定的证书（RFC 8705 §3/§6.2）。
func MTLSResourceCheck(tokenCnfX5t string, presentedDER []byte) (bool, string) {
	if tokenCnfX5t == "" {
		return true, "ok"
	}
	if X5TS256(presentedDER) != tokenCnfX5t {
		return false, "invalid_token"
	}
	return true, "ok"
}
