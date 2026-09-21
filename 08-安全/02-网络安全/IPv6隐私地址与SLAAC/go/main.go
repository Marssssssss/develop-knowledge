package main

import "fmt"

func hex64(v uint64) string {
	b := make([]byte, 8)
	for i := 0; i < 8; i++ {
		b[i] = byte(v >> (56 - 8*i))
	}
	return fmt.Sprintf("%x", b)
}

func main() {
	fmt.Println("== 1. RFC 8981 §3.8 默认参数 ==")
	fmt.Printf("   TEMP_VALID_LIFETIME     = %d s (2 天)\n", TempValidLifetime)
	fmt.Printf("   TEMP_PREFERRED_LIFETIME = %d s (1 天)\n", TempPreferredLifetime)
	fmt.Printf("   TEMP_IDGEN_RETRIES      = %d\n", TempIDGenRetries)
	fmt.Printf("   REGEN_ADVANCE           = %.0f s\n", DefaultRegenAdvance())
	fmt.Printf("   MAX_DESYNC_FACTOR       = %.0f s\n", MaxDesyncFactor())
	fmt.Printf("   重生成间隔(DESYNC=0/上限) = %.0f s / %.0f s\n",
		RegenerationInterval(0), RegenerationInterval(MaxDesyncFactor()))

	fmt.Println("\n== 2. RFC 4941 §3.2.1 的 MD5 方案 ==")
	mac := []byte{0x00, 0x11, 0x22, 0x33, 0x44, 0x55}
	pub := EUI64FromMAC(mac)
	fmt.Printf("   MAC -> EUI-64 = %s  U/L=%d\n", hex64(pub), (pub>>56)&ULBitMask != 0)
	hist := uint64(0x0123456789ABCDEF)
	for i := 0; i < 4; i++ {
		var iid uint64
		iid, hist = Md5IID(hist, pub)
		fmt.Printf("   第 %d 次 -> IID = %s  U/L=%d  history=%s\n",
			i+1, hex64(iid), (iid>>56)&ULBitMask != 0, hex64(hist))
	}

	fmt.Println("\n== 3. 生命周期（§3.3 step 4）：只有 preferred 减 DESYNC ==")
	for _, d := range []float64{0, 10000, MaxDesyncFactor()} {
		v, p := TempAddressLifetimes(2592000, 604800, d)
		fmt.Printf("   DESYNC=%-7.0f valid=%-7.0f preferred=%-7.0f\n", d, v, p)
	}

	fmt.Println("\n== 4. 是否创建（§3.3 step 5）==")
	for _, pref := range []float64{0, 4, 5, 6, 3600, 604800} {
		fmt.Printf("   前缀 preferred=%-7.0f -> 创建=%v\n", pref,
			ShouldCreateTempAddress(pref, 0))
	}
	fmt.Printf("   已有地址到期时刻 = %.0f\n", ClampExisting(0, 0, 604800, 0))

	fmt.Println("\n== 5. RFC 8981 §3.3.2 的 PRF 方案 ==")
	key := make([]byte, 32)
	for i := range key {
		key[i] = byte(i)
	}
	prefixHi, prefixLo := uint64(0x20010db8)<<32, uint64(0)
	rid := RidHmacSHA256(key, mac, []byte("ssid"), prefixHi, prefixLo, 1700000000, 0)
	fmt.Printf("   RID             = %x…\n", rid[:16])
	fmt.Printf("   IID(从最低位取) = %s\n", hex64(IIDFromRIDLow(rid)))
	fmt.Printf("   IID(从最高位取) = %s   <- 方向相反\n", hex64(IIDFromRIDHigh(rid)))
	r2 := RidHmacSHA256(key, mac, []byte("ssid"), prefixHi, prefixLo, 1700000001, 0)
	fmt.Printf("   Time+1          -> %s\n", hex64(IIDFromRIDLow(r2)))

	fmt.Println("\n== 6. DAD 冲突重试 ==")
	iid0, c0, _ := GenerateIIDRFC8981(key, mac, []byte("ssid"), prefixHi, prefixLo,
		1700000000, map[uint64]bool{}, TempIDGenRetries)
	iid1, c1, _ := GenerateIIDRFC8981(key, mac, []byte("ssid"), prefixHi, prefixLo,
		1700000000, map[uint64]bool{iid0: true}, TempIDGenRetries)
	fmt.Printf("   无冲突: IID=%s counter=%d\n", hex64(iid0), c0)
	fmt.Printf("   撞一次: IID=%s counter=%d\n", hex64(iid1), c1)
}
