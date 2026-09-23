package main

import (
	"encoding/hex"
	"fmt"
)

var total, failed int

func check(label string, cond bool, detail string) {
	total++
	if !cond {
		failed++
		fmt.Printf("  FAIL %s: %s\n", label, detail)
	}
}

func mustHex(s string) []byte {
	b, err := hex.DecodeString(s)
	if err != nil {
		panic(err)
	}
	return b
}

var (
	skE  = mustHex("52c4a758a802cd8b936eceea314432798d5baf2d7e9235dc084ab1b9cfa2f736")
	pkE  = mustHex("37fda3567bdbd628e88668c3c8d7e97d1d1253b6d4ea6d44c150f741f1bf4431")
	skR  = mustHex("4612c550263fc8ad58375df3f557aac531d26850903e55a9f23f21d8534e8ac8")
	pkR  = mustHex("3948cfe0ad1ddb695d780e59077195da6c56506b027329794ab02bca80815c4d")
	info = mustHex("4f6465206f6e2061204772656369616e2055726e")
)

func testX25519() {
	check("X25519(skE, G) == pkEm", hex.EncodeToString(X25519Base(skE)) == hex.EncodeToString(pkE), "")
	check("X25519(skR, G) == pkRm", hex.EncodeToString(X25519Base(skR)) == hex.EncodeToString(pkR), "")
	check("X25519 交换律", hex.EncodeToString(X25519(skE, pkR)) == hex.EncodeToString(X25519(skR, pkE)), "")
	c := ClampScalar(skE)
	check("clamp 清低 3 位", c[0]&7 == 0, "")
	check("clamp 清最高位", c[31]&0x80 == 0, "")
	check("clamp 置次高位", c[31]&0x40 == 0x40, "")
}

func testSuiteIDs() {
	check("KEM suite_id", hex.EncodeToString(kemSuiteID) == "4b454d0020", hex.EncodeToString(kemSuiteID))
	check("HPKE suite_id", hex.EncodeToString(hpkeSuiteID) == "48504b45002000010001",
		hex.EncodeToString(hpkeSuiteID))
}

func testBaseSetup() {
	ss, enc := Encap(pkR, skE)
	check("enc == pkEm", hex.EncodeToString(enc) == hex.EncodeToString(pkE), "")
	check("shared_secret 对上 A.1.1",
		hex.EncodeToString(ss) == "fe0e18c9f024ce43799ae393c7e8fe8fce9d218875e8227b0187c04e7d2ea1fc",
		hex.EncodeToString(ss))
	check("Decap 与 Encap 一致", hex.EncodeToString(Decap(enc, skR)) == hex.EncodeToString(ss), "")

	ks, err := KeySchedule(ModeBase, ss, info, nil, nil)
	if err != nil {
		check("KeySchedule 成功", false, err.Error())
		return
	}
	check("key_schedule_context 对上 A.1.1",
		hex.EncodeToString(ks.KeyScheduleCtx) == "00725611c9d98c07c03f60095cd32d400d8347d45ed67097bbad50fc56da742d07cb6cffde367bb0565ba28bb02c90744a20f5ef37f30523526106f637abb05449",
		hex.EncodeToString(ks.KeyScheduleCtx))
	check("secret 对上 A.1.1",
		hex.EncodeToString(ks.Secret) == "12fff91991e93b48de37e7daddb52981084bd8aa64289c3788471d9a9712f397", "")
	check("key 对上 A.1.1", hex.EncodeToString(ks.Key) == "4531685d41d65f03dc48f6b8302c05b0",
		hex.EncodeToString(ks.Key))
	check("base_nonce 对上 A.1.1", hex.EncodeToString(ks.BaseNonce) == "56d890e5accaaf011cff4b7d",
		hex.EncodeToString(ks.BaseNonce))
	check("exporter_secret 对上 A.1.1",
		hex.EncodeToString(ks.ExporterSecret) == "45ff1c2e220db587171952c0592d5f5ebe103f1561a2614e38f2ffd47e99e3f8", "")
}

func testExportAndNonce() {
	ss, _ := Encap(pkR, skE)
	ks, err := KeySchedule(ModeBase, ss, info, nil, nil)
	if err != nil {
		check("KeySchedule 成功", false, err.Error())
		return
	}
	ctx := &Context{BaseNonce: ks.BaseNonce, ExporterSecret: ks.ExporterSecret}
	cases := []struct{ in, want string }{
		{"", "3853fe2b4035195a573ffc53856e77058e15d9ea064de3e59f4961d0095250ee"},
		{"00", "2e8f0b54673c7029649d4eb9d5e33bf1872cf76d623ff164ac185da9e88c21a5"},
		{"54657374436f6e74657874", "e9e43065102c3836401bed8c3c3c75ae46be1639869391d62c61f1ec7af54931"},
	}
	for _, c := range cases {
		got := hex.EncodeToString(ctx.Export(mustHex(c.in), 32))
		check("Export("+c.in+") 对上 A.1.1.2", got == c.want, got)
	}
	check("nonce(seq=0)", hex.EncodeToString(ctx.ComputeNonce(0)) == "56d890e5accaaf011cff4b7d", "")
	check("nonce(seq=1)", hex.EncodeToString(ctx.ComputeNonce(1)) == "56d890e5accaaf011cff4b7c", "")
	check("nonce(seq=256) 翻倒数第二字节（I2OSP 大端）",
		hex.EncodeToString(ctx.ComputeNonce(256)) == "56d890e5accaaf011cff4a7d",
		hex.EncodeToString(ctx.ComputeNonce(256)))
}

func testPSK() {
	ss, _ := Encap(pkR, skE)
	psk := mustHex("0247fd33b913760fa1fa51e1892d9f307fbe65eb171e8132c2af18555a738b82")
	pskID := mustHex("456e6e796e20447572696e206172616e204d6f726961")
	_, err := KeySchedule(ModeBase, ss, info, psk, pskID)
	check("Base 模式带 PSK 被拒", err != nil, "")
	_, err2 := KeySchedule(ModePSK, ss, info, psk, nil)
	check("只给 psk 不给 psk_id 被拒", err2 != nil, "")
	ks, err3 := KeySchedule(ModePSK, ss, info, psk, pskID)
	check("PSK 模式可派生", err3 == nil && len(ks.Key) == 16 && len(ks.BaseNonce) == 12, "")
	base, _ := KeySchedule(ModeBase, ss, info, nil, nil)
	check("PSK 与 Base 的 key 不同", hex.EncodeToString(ks.Key) != hex.EncodeToString(base.Key), "")
}

func main() {
	fmt.Println("== HPKE（RFC 9180）Go 侧复刻自检 ==")
	testX25519()
	testSuiteIDs()
	testBaseSetup()
	testExportAndNonce()
	testPSK()
	fmt.Printf("\n断言 %d 条，失败 %d 条\n", total, failed)
	if failed > 0 {
		panic("有断言失败")
	}
}
