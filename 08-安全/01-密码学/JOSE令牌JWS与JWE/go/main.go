package main

import (
	"encoding/base64"
	"fmt"
)

func main() {
	fmt.Println("1. 官方 JWS（RFC 7515 §3.3）")
	tok := "eyJ0eXAiOiJKV1QiLA0KICJhbGciOiJIUzI1NiJ9." +
		"eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6Ly9leGFt" +
		"cGxlLmNvbS9pc19yb290Ijp0cnVlfQ." +
		"dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
	key, _ := base64.RawURLEncoding.DecodeString("AyM1SysPpbyDfgZld3umj1qzKObwVMko" +
		"qQ-EstJQLr_T-1qS0gZH75aKtMN3Yj0iPS4hcgUuTwjAzZr1Z9CAow")
	payload, err := JWSVerify(tok, key, []string{"HS256"}, "")
	fmt.Printf("   验签: %s err=%v\n", payload, err)
	_, err = JWSVerify(tok, key, []string{"RS256"}, "")
	fmt.Println("   限定 RS256 ->", err)
	_, err = JWSVerify(tok, key, []string{"HS256"}, "RS256")
	fmt.Println("   密钥 alg=RS256 ->", err)

	fmt.Println()
	fmt.Println("2. 官方 JWE（RFC 7516 A.3）：A128KW + A128CBC-HS256")
	kek, _ := base64.RawURLEncoding.DecodeString("GawgguFyGrWKav7AX4VKUg")
	cek := []byte{4, 211, 31, 197, 84, 157, 252, 254, 11, 100, 157, 250, 63, 170, 106,
		206, 107, 124, 212, 45, 111, 107, 9, 219, 200, 177, 0, 240, 143, 156, 44, 207}
	iv := []byte{3, 22, 60, 12, 43, 67, 104, 105, 108, 108, 105, 99, 111, 116, 104, 101}
	hdr := map[string]interface{}{"alg": "A128KW", "enc": "A128CBC-HS256"}
	jwe, err := JWEEncrypt(hdr, []byte("Live long and prosper."), kek, cek, iv)
	fmt.Println("   JWE:", jwe)
	fmt.Println("   err:", err)
	official := "eyJhbGciOiJBMTI4S1ciLCJlbmMiOiJBMTI4Q0JDLUhTMjU2In0." +
		"6KB707dM9YTIgHtLvtgWQ8mKwboJW3of9locizkDTHzBC2IlrT1oOQ." +
		"AxY8DCtDaGlsbGljb3RoZQ." +
		"KDlTtXchhZTGufMYmOYGS4HffxPSUrfmqCHXaI9wOGY." +
		"U0m_YmjN04DJvceFICbCVQ"
	fmt.Println("   与 RFC 7516 A.3 官方串相同:", jwe == official)
	plain, err := JWEDecrypt(official, kek, nil)
	fmt.Printf("   解密: %q err=%v\n", plain, err)

	fmt.Println()
	fmt.Println("3. alg=none")
	noneTok, _ := JWSSign(map[string]interface{}{"alg": "none"},
		[]byte(`{"admin":true}`), nil, []string{"none"})
	fmt.Println("   token:", noneTok)
	_, err = JWSVerify(noneTok, nil, []string{"HS256"}, "")
	fmt.Println("   按 HS256 验 ->", err)
	got, err := JWSVerify(noneTok, nil, []string{"none"}, "")
	fmt.Printf("   显式允许 none -> %s err=%v\n", got, err)
}
