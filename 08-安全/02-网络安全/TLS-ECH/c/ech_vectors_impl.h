/*
 * ech_vectors_impl.h —— RFC 9180 附录 A.2 官方向量（本 demo 唯一有权威期望值的部分）
 *
 * 其余用例都是「构造—破坏—必须失败」或可由规范直接推出的等式 —— RFC 9849 没有
 * 官方向量，硬编码魔数只会把实现 bug 固化成期望值。
 *
 * A.2 的套件恰好是本 demo 选用的 DHKEM(X25519, HKDF-SHA256) / HKDF-SHA256 /
 * ChaCha20-Poly1305，所以这 28 条断言直接覆盖了 KEM、密钥计划、AEAD 与 Export 四层。
 */
#ifndef ECH_VECTORS_IMPL_H
#define ECH_VECTORS_IMPL_H

#include "ech_cmp_impl.h"

#define VEC_IKM_E "909a9b35d3dc4713a5e72a4da274b55d3d3821a37e5d099e74a647db583a904b"
#define VEC_SK_E "f4ec9b33b792c372c1d2c2063507b684ef925b8c75a42dbcbf57d63ccd381600"
#define VEC_PK_E "1afa08d3dec047a643885163f1180476fa7ddb54c6a8029ea33f95796bf2ac4a"
#define VEC_IKM_R "1ac01f181fdf9f352797655161c58b75c656a6cc2716dcb66372da835542e1df"
#define VEC_SK_R "8057991eef8f1f1af18f4a9491d16a1ce333f695d4db8e38da75975c4478e0fb"
#define VEC_PK_R "4310ee97d88cc1f088a5576c77ab0cf5c3ac797f3d95139c6c84b5429c59662a"
#define VEC_INFO "4f6465206f6e2061204772656369616e2055726e"
#define VEC_PT "4265617574792069732074727574682c20747275746820626561757479"
#define VEC_SS "0bbe78490412b4bbea4812666f7916932b828bba79942424abb65244930d69a7"
#define VEC_KSC "00431df6cd95e11ff49d7013563baf7f11588c75a6611ee2a4404a49306ae4cf" \
                "c5b69c5718a60cc5876c358d3f7fc31ddb598503f67be58ea1e798c0bb19eb9796"
#define VEC_SECRET "5b9cd775e64b437a2335cf499361b2e0d5e444d5cb41a8a53336d8fe402282c6"
#define VEC_KEY "ad2744de8e17f4ebba575b3f5f5a8fa1f69c2a07f6e7500bc60ca6e3e3ec1c91"
#define VEC_NONCE "5c4d98150661b848853b547f"
#define VEC_EXPORTER "a3b010d4994890e2c6968a36f64470d3c824c8f5029942feb11e7a74b2921922"
#define VEC_EXP_EMPTY "4bbd6243b8bb54cec311fac9df81841b6fd61f56538a775e7c80a9f40160606e"
#define VEC_EXP_00 "8c1df14732580e5501b00f82b10a1647b40713191b7c1240ac80e2b68808ba69"
#define VEC_EXP_CTX "5acb09211139c43b3090489a9da433e8a30ee7188ba8b0a9a1ccf0c229283e53"
#define VEC_CTX_INFO "54657374436f6e74657874"

/* 加密用例：密文含 16 字节 tag，明文 29 字节 → 45 字节。序号跨 0/1/2/4/255/256 */
#define VEC_AAD_0 "436f756e742d30"
#define VEC_CT_0 "1c5250d8034ec2b784ba2cfd69dbdb8af406cfe3ff938e131f0def8c8b60b4db21993c62ce81883d2dd1b51a28"
#define VEC_AAD_1 "436f756e742d31"
#define VEC_CT_1 "6b53c051e4199c518de79594e1c4ab18b96f081549d45ce015be002090bb119e85285337cc95ba5f59992dc98c"
#define VEC_AAD_2 "436f756e742d32"
#define VEC_CT_2 "71146bd6795ccc9c49ce25dda112a48f202ad220559502cef1f34271e0cb4b02b4f10ecac6f48c32f878fae86b"
#define VEC_AAD_4 "436f756e742d34"
#define VEC_CT_4 "63357a2aa291f5a4e5f27db6baa2af8cf77427c7c1a909e0b37214dd47db122bb153495ff0b02e9e54a50dbe16"
#define VEC_AAD_255 "436f756e742d323535"
#define VEC_CT_255 "18ab939d63ddec9f6ac2b60d61d36a7375d2070c9b683861110757062c52b8880a5f6b3936da9cd6c23ef2a95c"
#define VEC_AAD_256 "436f756e742d323536"
#define VEC_CT_256 "7a4a13e9ef23978e2c520fd4d2e757514ae160cd0cd05e556ef692370ca53076214c0c40d4c728d6ed9e727a5b"

static int check_hpke_vectors(void)
{
    static uint8_t ikm_r[32], ikm_e[32], sk_r[32], pk_r[32], sk_e[32], pk_e[32];
    static uint8_t ss[ECH_NSECRET], enc[32], info[64], pt[64], aad[64], ct[64], out[64];
    ech_context ctx;
    uint8_t psk_id_hash[ECH_NH], info_hash[ECH_NH], secret[ECH_NH];
    uint8_t ksc[1 + 2 * ECH_NH];
    const int seqs[6] = {0, 1, 2, 4, 255, 256};
    const char *const aads[6] = {VEC_AAD_0, VEC_AAD_1, VEC_AAD_2,
                                 VEC_AAD_4, VEC_AAD_255, VEC_AAD_256};
    const char *const cts[6] = {VEC_CT_0, VEC_CT_1, VEC_CT_2,
                                VEC_CT_4, VEC_CT_255, VEC_CT_256};
    int before = g_checks;
    size_t info_len, pt_len, ctx_info_len, i;
    int n;

    unhex(VEC_IKM_R, ikm_r, 32);
    unhex(VEC_IKM_E, ikm_e, 32);
    info_len = unhex(VEC_INFO, info, sizeof(info));
    pt_len = unhex(VEC_PT, pt, sizeof(pt));

    /* §7.1.3 DeriveKeyPair：X25519 不做拒绝采样，一次 LabeledExpand 就是私钥 */
    check("derive_key_pair(ikmR).sk == skRm",
          ech_derive_key_pair(ikm_r, sk_r, pk_r) == 0 && eq_hex(sk_r, 32, VEC_SK_R), "");
    check("derive_key_pair(ikmR).pk == pkRm", eq_hex(pk_r, 32, VEC_PK_R), "");
    check("derive_key_pair(ikmE).sk == skEm",
          ech_derive_key_pair(ikm_e, sk_e, pk_e) == 0 && eq_hex(sk_e, 32, VEC_SK_E), "");
    check("derive_key_pair(ikmE).pk == pkEm", eq_hex(pk_e, 32, VEC_PK_E), "");

    /* Encap：enc 是**发送方**的临时公钥（pkEm），不是接收方的 pkRm */
    check("Encap 的 enc == pkEm",
          ech_encap(pk_r, ikm_e, enc, ss) == 0 && eq_hex(enc, 32, VEC_PK_E), "");
    if (!eq_hex(ss, 32, VEC_SS)) {
        dump_hex("shared_secret", ss, 32);
    }
    check("Encap 的 shared_secret == A.2 向量", eq_hex(ss, 32, VEC_SS), "");
    check("Decap 得到同一 shared_secret",
          ech_decap(enc, sk_r, pk_r, out) == 0 && memcmp(out, ss, 32) == 0, "");

    /* §5.1 密钥计划里三个 LabeledExtract 的盐互不相同：psk_id_hash / info_hash 用空串，
     * secret 用 shared_secret；ksc = mode_base | psk_id_hash | info_hash */
    check("KeySchedule 成功", ech_key_schedule(ss, info, info_len, &ctx) == 0, "");
    (void)ech_labeled_extract(NULL, 0, ECH_HPKE_SUITE_ID, ECH_HPKE_SUITE_LEN, "psk_id_hash",
                              NULL, 0, psk_id_hash);
    (void)ech_labeled_extract(NULL, 0, ECH_HPKE_SUITE_ID, ECH_HPKE_SUITE_LEN, "info_hash",
                              info, info_len, info_hash);
    ksc[0] = 0x00;
    memcpy(ksc + 1, psk_id_hash, ECH_NH);
    memcpy(ksc + 1 + ECH_NH, info_hash, ECH_NH);
    check("key_schedule_context == A.2 向量", eq_hex(ksc, sizeof(ksc), VEC_KSC), "");
    (void)ech_labeled_extract(ss, ECH_NSECRET, ECH_HPKE_SUITE_ID, ECH_HPKE_SUITE_LEN,
                              "secret", NULL, 0, secret);
    if (!eq_hex(secret, ECH_NH, VEC_SECRET)) {
        dump_hex("secret", secret, ECH_NH);
    }
    check("secret == A.2 向量（盐是 shared_secret）", eq_hex(secret, ECH_NH, VEC_SECRET), "");
    check("key == A.2 向量", eq_hex(ctx.key, ECH_NK, VEC_KEY), "");
    check("base_nonce == A.2 向量", eq_hex(ctx.base_nonce, ECH_NN, VEC_NONCE), "");
    check("exporter_secret == A.2 向量", eq_hex(ctx.exporter_secret, ECH_NH, VEC_EXPORTER), "");

    /* §6.1 Seal / Open：序号覆盖「XOR 只影响末字节」到「推进到倒数第二字节」的边界 */
    for (i = 0; i < 6; i++) {
        size_t aad_len = unhex(aads[i], aad, sizeof(aad));
        size_t want = unhex(cts[i], ct, sizeof(ct));
        ctx.seq = (uint64_t)seqs[i];
        n = ech_ctx_seal(&ctx, aad, aad_len, pt, pt_len, out);
        check("Seal 密文 == A.2 向量", n == (int)want && memcmp(out, ct, want) == 0, "");
        ctx.seq = (uint64_t)seqs[i];
        n = ech_ctx_open(&ctx, aad, aad_len, ct, want, out);
        check("Open 还原明文", n == (int)pt_len && memcmp(out, pt, pt_len) == 0, "");
    }

    /* §6.1 Export：ctx_info 为空串 / 单字节 00 / 11 字节三种 */
    ctx_info_len = unhex(VEC_CTX_INFO, info, sizeof(info));
    check("Export(ctx_info=\"\") == A.2 向量",
          ech_ctx_export(&ctx, NULL, 0, out, 32) == 0 && eq_hex(out, 32, VEC_EXP_EMPTY), "");
    check("Export(ctx_info=00) == A.2 向量",
          ech_ctx_export(&ctx, (const uint8_t *)"\x00", 1, out, 32) == 0 &&
              eq_hex(out, 32, VEC_EXP_00), "");
    check("Export(ctx_info=TestContext) == A.2 向量",
          ctx_info_len == 11 && ech_ctx_export(&ctx, info, ctx_info_len, out, 32) == 0 &&
              eq_hex(out, 32, VEC_EXP_CTX), "");
    return g_checks - before;
}

#endif /* ECH_VECTORS_IMPL_H */
