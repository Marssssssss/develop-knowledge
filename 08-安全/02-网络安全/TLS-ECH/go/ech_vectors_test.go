// ech_vectors_test.go —— A 组：RFC 9180 附录 A.2 官方向量
//
// 这是本 demo 唯一有**权威期望值**的部分。RFC 9849 没有测试向量，所以其余各组的判据
// 都是「构造—破坏—必须失败」或可由规范直接推出的等式 —— 硬编码魔数只会把实现 bug
// 固化成期望值。
//
// A.2 的套件恰好就是本 demo 选用的 DHKEM(X25519, HKDF-SHA256) / HKDF-SHA256 /
// ChaCha20-Poly1305，所以这 28 条断言直接覆盖 KEM、密钥计划、AEAD 与 Export 四层。
package main

import (
	"bytes"
	"strconv"
)

const (
	vecIKME      = "909a9b35d3dc4713a5e72a4da274b55d3d3821a37e5d099e74a647db583a904b"
	vecSKE       = "f4ec9b33b792c372c1d2c2063507b684ef925b8c75a42dbcbf57d63ccd381600"
	vecPKE       = "1afa08d3dec047a643885163f1180476fa7ddb54c6a8029ea33f95796bf2ac4a"
	vecIKMR      = "1ac01f181fdf9f352797655161c58b75c656a6cc2716dcb66372da835542e1df"
	vecSKR       = "8057991eef8f1f1af18f4a9491d16a1ce333f695d4db8e38da75975c4478e0fb"
	vecPKR       = "4310ee97d88cc1f088a5576c77ab0cf5c3ac797f3d95139c6c84b5429c59662a"
	vecInfo      = "4f6465206f6e2061204772656369616e2055726e"
	vecPT        = "4265617574792069732074727574682c20747275746820626561757479"
	vecSS        = "0bbe78490412b4bbea4812666f7916932b828bba79942424abb65244930d69a7"
	vecKSC       = "00431df6cd95e11ff49d7013563baf7f11588c75a6611ee2a4404a49306ae4cf" +
		"c5b69c5718a60cc5876c358d3f7fc31ddb598503f67be58ea1e798c0bb19eb9796"
	vecSecret    = "5b9cd775e64b437a2335cf499361b2e0d5e444d5cb41a8a53336d8fe402282c6"
	vecKey       = "ad2744de8e17f4ebba575b3f5f5a8fa1f69c2a07f6e7500bc60ca6e3e3ec1c91"
	vecNonce     = "5c4d98150661b848853b547f"
	vecExporter  = "a3b010d4994890e2c6968a36f64470d3c824c8f5029942feb11e7a74b2921922"
	vecExpEmpty  = "4bbd6243b8bb54cec311fac9df81841b6fd61f56538a775e7c80a9f40160606e"
	vecExpZero   = "8c1df14732580e5501b00f82b10a1647b40713191b7c1240ac80e2b68808ba69"
	vecExpCtx    = "5acb09211139c43b3090489a9da433e8a30ee7188ba8b0a9a1ccf0c229283e53"
	vecCtxInfo   = "54657374436f6e74657874"
	vecAAD0      = "436f756e742d30"
	vecCT0       = "1c5250d8034ec2b784ba2cfd69dbdb8af406cfe3ff938e131f0def8c8b60b4db21993c62ce81883d2dd1b51a28"
	vecAAD1      = "436f756e742d31"
	vecCT1       = "6b53c051e4199c518de79594e1c4ab18b96f081549d45ce015be002090bb119e85285337cc95ba5f59992dc98c"
	vecAAD2      = "436f756e742d32"
	vecCT2       = "71146bd6795ccc9c49ce25dda112a48f202ad220559502cef1f34271e0cb4b02b4f10ecac6f48c32f878fae86b"
	vecAAD4      = "436f756e742d34"
	vecCT4       = "63357a2aa291f5a4e5f27db6baa2af8cf77427c7c1a909e0b37214dd47db122bb153495ff0b02e9e54a50dbe16"
	vecAAD255    = "436f756e742d323535"
	vecCT255     = "18ab939d63ddec9f6ac2b60d61d36a7375d2070c9b683861110757062c52b8880a5f6b3936da9cd6c23ef2a95c"
	vecAAD256    = "436f756e742d323536"
	vecCT256     = "7a4a13e9ef23978e2c520fd4d2e757514ae160cd0cd05e556ef692370ca53076214c0c40d4c728d6ed9e727a5b"
)

func checkHPKEVectors() int {
	before := checksTotal
	ikmE, ikmR := mustHex(vecIKME), mustHex(vecIKMR)
	info, pt := mustHex(vecInfo), mustHex(vecPT)

	// §7.1.3 DeriveKeyPair：X25519 不做拒绝采样，一次 LabeledExpand 就是私钥
	skR, pkR, err := deriveKeyPair(ikmR)
	check("derive_key_pair(ikmR).sk == skRm", err == nil && eqHex(skR, vecSKR), "")
	check("derive_key_pair(ikmR).pk == pkRm", eqHex(pkR, vecPKR), "")
	skE, pkE, err := deriveKeyPair(ikmE)
	check("derive_key_pair(ikmE).sk == skEm", err == nil && eqHex(skE, vecSKE), "")
	check("derive_key_pair(ikmE).pk == pkEm", eqHex(pkE, vecPKE), "")

	// Encap：enc 是**发送方**的临时公钥（pkEm），不是接收方的 pkRm
	enc, ss, err := encap(pkR, ikmE)
	check("Encap 的 enc == pkEm", err == nil && eqHex(enc, vecPKE), "")
	check("Encap 的 shared_secret == A.2 向量", eqHex(ss, vecSS), "got="+hexOf(ss))
	ss2, err := decap(enc, skR, pkR)
	check("Decap 得到同一 shared_secret", err == nil && bytes.Equal(ss2, ss), "")

	// §5.1 密钥计划里三个 LabeledExtract 的盐互不相同
	ks := newKeySchedule(ss, info)
	check("key_schedule_context == A.2 向量", eqHex(ks.KeyScheduleContext, vecKSC), "")
	check("secret（盐是 shared_secret）== A.2 向量", eqHex(ks.Secret, vecSecret), "")
	check("key == A.2 向量", eqHex(ks.Key, vecKey), "")
	check("base_nonce == A.2 向量", eqHex(ks.BaseNonce, vecNonce), "")
	check("exporter_secret == A.2 向量", eqHex(ks.ExporterSecret, vecExporter), "")

	// §6.1 Seal / Open：序号跨 0/1/2/4/255/256
	ctx := &hpkeContext{ks: ks}
	cases := []struct {
		seq  uint64
		aad  string
		want string
	}{
		{0, vecAAD0, vecCT0}, {1, vecAAD1, vecCT1}, {2, vecAAD2, vecCT2},
		{4, vecAAD4, vecCT4}, {255, vecAAD255, vecCT255}, {256, vecAAD256, vecCT256},
	}
	for _, c := range cases {
		ctx.Seq = c.seq
		ct, err := ctx.Seal(mustHex(c.aad), pt)
		check("Seal 密文 == A.2 向量", err == nil && eqHex(ct, c.want), "seq="+strconv.FormatUint(c.seq, 10))
		ctx.Seq = c.seq
		got, err := ctx.Open(mustHex(c.aad), mustHex(c.want))
		check("Open 还原明文", err == nil && bytes.Equal(got, pt), "seq="+strconv.FormatUint(c.seq, 10))
	}

	// §6.1 Export：ctx_info 为空串 / 单字节 00 / 11 字节三种
	_, ctx2, err := setupBaseS(pkR, info, ikmE)
	check("setupBaseS 成功", err == nil, "")
	check("Export(ctx_info=\"\") == A.2 向量",
		bytes.Equal(ctx2.Export(nil, 32), mustHex(vecExpEmpty)), "")
	check("Export(ctx_info=00) == A.2 向量",
		bytes.Equal(ctx2.Export([]byte{0x00}, 32), mustHex(vecExpZero)), "")
	check("Export(ctx_info=TestContext) == A.2 向量",
		bytes.Equal(ctx2.Export(mustHex(vecCtxInfo), 32), mustHex(vecExpCtx)), "")
	return checksTotal - before
}
