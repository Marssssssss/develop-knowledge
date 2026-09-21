package main

import (
	"fmt"
	"math/big"
)

func mpintHex(v int64) string {
	b := Mpint(big.NewInt(v))
	if len(b) == 0 {
		return "(空串)"
	}
	h := ""
	for _, c := range b {
		h += fmt.Sprintf("%02x", c)
	}
	return h
}

func main() {
	fmt.Println("== 1. mpint 编码（RFC 4251 §5）==")
	for _, v := range []int64{0, 127, 128, 255, -1, -128, -129} {
		fmt.Printf("   %-6d -> %s\n", v, mpintHex(v))
	}

	fmt.Println("\n== 2. padding 与整包长度（§6）==")
	fmt.Println("   payload  blk  padding  packet_length  整包(不含mac)")
	for _, L := range []int{0, 1, 7, 10, 11, 16, 100} {
		for _, blk := range []int{8, 16} {
			pad := PaddingLength(L, blk)
			fmt.Printf("   %-8d %-4d %-8d %-14d %d\n", L, blk, pad, pad+1+L, 4+1+L+pad)
		}
	}

	fmt.Println("\n== 3. 组包/解包往返 ==")
	mac := make([]byte, 32)
	for i := range mac {
		mac[i] = 0xAA
	}
	raw := BuildPacket([]byte("hello"), 8, mac)
	p, err := ParsePacket(raw, 8, 32)
	if err != nil {
		fmt.Println("   解析失败:", err)
		return
	}
	fmt.Printf("   载荷往返: %q  padding=%d packet_length=%d\n",
		string(p.Payload), p.PaddingLength, p.PacketLength)

	fmt.Println("\n== 4. 算法协商（§7.1）==")
	caps := map[string]map[string]bool{
		"ssh-rsa":    {"sign": true, "encrypt": true},
		"ssh-dss":    {"sign": true},
		"ssh-ed25519": {"sign": true},
	}
	kexs := []Alg{
		{Name: "rsa2048-sha256", Needs: "encrypt"},
		{Name: "curve25519-sha256"},
		{Name: "diffie-hellman-group14-sha1"},
	}
	for _, hk := range []string{"ssh-ed25519", "ssh-rsa"} {
		got := SelectKex(kexs, kexs, []string{hk}, []string{hk}, caps)
		name := "<nil>"
		if got != nil {
			name = got.Name
		}
		fmt.Printf("   主机密钥=%-12s -> KEX=%s\n", hk, name)
	}
	fmt.Printf("   cipher 选法（只看名字）: %s\n",
		SelectFirstMatch([]string{"aes256-ctr", "aes128-ctr"},
			[]string{"aes128-ctr", "3des-cbc"}))

	fmt.Println("\n== 5. 密钥派生（§7.2）==")
	K := Mpint(big.NewInt(0xABCDEF))
	h1 := make([]byte, 32)
	h2 := make([]byte, 32)
	for i := range h1 {
		h1[i] = 0x11
		h2[i] = 0x22
	}
	k1 := deriveAll(K, h1, h1)
	k2 := deriveAll(K, h2, h1)
	for _, name := range []string{"enc_c2s", "enc_s2c", "iv_c2s", "iv_s2c", "mac_c2s", "mac_s2c"} {
		fmt.Printf("   %-8s 首轮=%x… rekey=%x… 变了=%v\n",
			name, k1[name][:6], k2[name][:6], string(k1[name]) != string(k2[name]))
	}

	fmt.Println("\n== 6. exchange hash（§8）==")
	h := ExchangeHash([]byte("SSH-2.0-C"), []byte("SSH-2.0-S"),
		[]byte("I_C"), []byte("I_S"), []byte("K_S"),
		big.NewInt(5), big.NewInt(7), big.NewInt(35))
	fmt.Printf("   H = %x…\n", h[:12])
	hs := ExchangeHash([]byte("SSH-2.0-S"), []byte("SSH-2.0-C"),
		[]byte("I_C"), []byte("I_S"), []byte("K_S"),
		big.NewInt(5), big.NewInt(7), big.NewInt(35))
	fmt.Printf("   互换 V_C/V_S 后不同: %v\n", string(hs) != string(h))

	fmt.Println("\n== 7. RFC 8308 扩展协商 ==")
	fmt.Printf("   客户端放 ext-info-c: %v\n", OffersExtInfo([]string{"ext-info-c"}, "client"))
	fmt.Printf("   服务端放 ext-info-c 不算数: %v\n",
		!OffersExtInfo([]string{"ext-info-c"}, "server"))
	fmt.Printf("   正常协商: %q\n", ExtNegotiationError("curve25519-sha256",
		[]string{"ext-info-c"}, []string{"ext-info-s"}))
	fmt.Printf("   indicator 被协商成 KEX: %q\n", ExtNegotiationError("ext-info-c",
		[]string{"ext-info-c"}, []string{"ext-info-c"}))
	fmt.Printf("   对端没给 indicator 时能否发 EXT_INFO: %v\n", ExtInfoAllowed(false))
}
