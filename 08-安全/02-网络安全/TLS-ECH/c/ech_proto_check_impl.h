/*
 * ech_proto_check_impl.h —— D 端到端往返 与 E 篡改必然失败
 *
 * 这一组的判据不是魔数而是**性质**：
 *   D  线上看不到真正的 SNI；服务端还原出的 ClientHelloInner 与客户端输入逐字节相同；
 *      legacy_session_id 以 ClientHelloOuter 为准。
 *   E  任何被 AAD 覆盖的字段被改动 → Open 认证失败；config_id 不匹配 → 原因码 -2
 *      （服务端应换下一个 ECHConfig 重试）；填充非零 → 原因码 -3。
 * 期望值在 Python（hpke.HpkeError / EchError）与 C（-1/-2/-3）里口径不同，见 README。
 */
#ifndef ECH_PROTO_CHECK_IMPL_H
#define ECH_PROTO_CHECK_IMPL_H

#include "ech_codec_check_impl.h"

static int check_roundtrip(void)
{
    static ech_config cfg;
    static ech_client_hello inner, tpl, tpl_b, outer, outer_b, srv, aad_like, cleared;
    static ech_context ctx;
    static uint8_t encoded[ECH_SCRATCH], inner_wire[512], outer_wire[1024];
    static uint8_t aad_wire[1024], zero_payload[512];
    static uint8_t pk[32], sk[32], rnd[32], rnd_b[32], sid[32], sid_b[32];
    const uint8_t *enc = NULL, *payload = NULL;
    size_t enc_len = 0, clean_len = 0, outer_len = 0, aad_len = 0, e_len = 0, p_len = 0;
    size_t i, pad;
    uint16_t kdf_id = 0, aead_id = 0;
    uint8_t cid = 0;
    int before = g_checks, at, rc;

    unhex(VEC_PK_R, pk, 32);
    unhex(VEC_SK_R, sk, 32);
    t_make_config(&cfg, T_CONFIG_ID, pk);
    t_fill(rnd, 32, 0x55);
    t_fill(rnd_b, 32, 0x77);
    t_fill(sid, 32, 0x66);
    t_fill(sid_b, 32, 0x99);
    memset(zero_payload, 0, sizeof(zero_payload));
    t_make_inner(&inner, rnd, sid, 32);
    t_make_outer_tpl(&tpl, rnd_b, sid, 32);

    rc = ech_client_encrypt(&outer, &ctx, encoded, &enc_len, sizeof(encoded), &inner, &tpl,
                            &cfg, NULL, 0);
    check("客户端封装成功", rc == 0, "");
    if (rc != 0) {
        return g_checks - before;
    }

    cleared = inner;
    cleared.session_id_len = 0;
    check("清空 legacy_session_id 后编码成功",
          ch_encode(&cleared, inner_wire, sizeof(inner_wire), &clean_len) == 0, "");
    check("EncodedClientHelloInner 长度是 32 的倍数（§6.1.3 第 2 步）",
          enc_len > clean_len && enc_len % 32 == 0, "");
    check("前缀就是清空 session_id 的 ClientHelloInner",
          memcmp(encoded, inner_wire, clean_len) == 0, "");
    pad = enc_len - clean_len;
    for (i = 0; i < pad && encoded[clean_len + i] == 0x00; i++) {
    }
    check("其余全部是零填充", i == pad, "");

    at = ch_ext_index(&outer, ECH_EXT_TYPE);
    check("ClientHelloOuter 带 ECH 扩展且能按 outer 变体解出字段",
          at >= 0 && ech_outer_ext_decode(outer.exts[at].data, outer.exts[at].len, &kdf_id,
                                          &aead_id, &cid, &enc, &e_len, &payload,
                                          &p_len) == 0, "");
    check("config_id 与套件与 ECHConfig 一致",
          cid == T_CONFIG_ID && kdf_id == ECH_KDF_ID_HKDF_SHA256 &&
              aead_id == ECH_AEAD_ID_CHACHA20POLY1305, "");
    check("enc 是 32 字节 X25519 公钥；payload = 密文 + 16 字节 tag",
          e_len == 32 && p_len == enc_len + ECH_TAG_LEN, "");
    check("客户端只 Seal 了一次（§6.1.1 只做一次密钥封装）", ctx.seq == 1, "");

    /* AAD 与最终 ClientHelloOuter 等长，所以把占位换成真密文时不用重算任何长度前缀 */
    aad_like = outer;
    check("payload 换成等长零后总长度不变（AAD 可以先算后填）",
          t_rebuild_ech_ext(&aad_like, zero_payload, p_len) == 0 &&
              ch_encode(&outer, outer_wire, sizeof(outer_wire), &outer_len) == 0 &&
              ch_encode(&aad_like, aad_wire, sizeof(aad_wire), &aad_len) == 0 &&
              aad_len == outer_len, "");

    check("服务端解封成功", ech_server_decrypt(&srv, &outer, sk, pk, &cfg) == 0, "");
    check("ClientHelloInner.random 还原", memcmp(srv.random, rnd, 32) == 0, "");
    check("legacy_session_id 从 ClientHelloOuter 抄回",
          srv.session_id_len == 32 && memcmp(srv.session_id, sid, 32) == 0, "");
    check("还原后与 ClientHelloInner 逐字段一致（含扩展顺序）", same_ch(&srv, &inner), "");
    check("真正的 SNI 不出现在线上字节里", has_sub(outer_wire, outer_len, T_SNI) == 0, "");
    check("线上只有 public_name", has_sub(outer_wire, outer_len, T_PUBLIC_NAME) == 1, "");

    t_make_outer_tpl(&tpl_b, rnd_b, sid_b, 32);
    check("session_id 一律以 ClientHelloOuter 为准（与 inner 里放的值无关）",
          ech_client_encrypt(&outer_b, &ctx, encoded, &enc_len, sizeof(encoded), &inner,
                             &tpl_b, &cfg, NULL, 0) == 0 &&
              ech_server_decrypt(&srv, &outer_b, sk, pk, &cfg) == 0 &&
              srv.session_id_len == 32 && memcmp(srv.session_id, sid_b, 32) == 0, "");
    return g_checks - before;
}

static int check_tamper(void)
{
    static ech_config cfg, wrong_id;
    static ech_client_hello inner, tpl, outer, twisted, srv;
    static ech_context ctx;
    static uint8_t encoded[ECH_SCRATCH], wire[1024], dirty[512], sn_ext[64];
    static uint8_t pk[32], sk[32], other_sk[32], other_pk[32], other_ikm[32];
    static uint8_t rnd[32], sid[32];
    size_t enc_len = 0, wire_len = 0, n = 0, consumed = 0;
    int before = g_checks, rc;

    unhex(VEC_PK_R, pk, 32);
    unhex(VEC_SK_R, sk, 32);
    t_make_config(&cfg, T_CONFIG_ID, pk);
    t_make_config(&wrong_id, T_CONFIG_ID ^ 0x01, pk);
    t_fill(other_ikm, 32, 0x00);
    (void)ech_derive_key_pair(other_ikm, other_sk, other_pk);
    t_fill(rnd, 32, 0x55);
    t_fill(sid, 32, 0x66);
    t_make_inner(&inner, rnd, sid, 32);
    t_make_outer_tpl(&tpl, rnd, sid, 32);

    rc = ech_client_encrypt(&outer, &ctx, encoded, &enc_len, sizeof(encoded), &inner, &tpl,
                            &cfg, NULL, 0);
    check("（前置）未篡改时解封成功",
          rc == 0 && ech_server_decrypt(&srv, &outer, sk, pk, &cfg) == 0, "");
    if (rc != 0) {
        return g_checks - before;
    }

    twisted = outer;
    twisted.cipher_suites_len = 1;
    twisted.cipher_suites[0] = 0x1301;
    check("改 cipher_suites → 认证失败（-1）",
          ech_server_decrypt(&srv, &twisted, sk, pk, &cfg) == -1, "");

    twisted = outer;
    n = t_server_name_ext(sn_ext, "attacker.example");
    check("改 ClientHelloOuter 的 SNI → 认证失败（-1）",
          ch_set_ext(&twisted, ECH_EXT_SERVER_NAME, sn_ext, n) == 0 &&
              ech_server_decrypt(&srv, &twisted, sk, pk, &cfg) == -1, "");

    /* payload 是 ECH 扩展的最后一个字段，而 ECH 扩展又是整个 ClientHello 的最后一个
     * 扩展 —— 所以线上最后一个字节一定落在 payload 里，翻转它必然破坏 AEAD 的 tag */
    check("（前置）整体编码成功",
          ch_encode(&outer, wire, sizeof(wire), &wire_len) == 0, "");
    wire[wire_len - 1] ^= 0x01;
    twisted = outer;
    check("翻转 payload 末字节 → 认证失败（-1）",
          ch_decode(&twisted, wire, wire_len, &consumed) == 0 &&
              ech_server_decrypt(&srv, &twisted, sk, pk, &cfg) == -1, "");

    check("config_id 不匹配 → 试解密失败（-2，服务端应换下一个 ECHConfig）",
          ech_server_decrypt(&srv, &outer, sk, pk, &wrong_id) == -2, "");
    check("换一把私钥（config_id 相同）→ 密钥计划不同，认证失败（-1）",
          ech_server_decrypt(&srv, &outer, other_sk, pk, &cfg) == -1, "");

    /* §5.1：EncodedClientHelloInner 尾部的填充必须全零 */
    check("（前置）构造尾部填充非零的 EncodedClientHelloInner",
          ech_encoded_inner(&inner, 8, dirty, sizeof(dirty), &n) == 0 && n > 8, "");
    dirty[n - 1] = 0x01;
    check("非零填充 → 原因码 -3",
          t_encrypt_raw(&twisted, &tpl, &cfg, dirty, n) == 0 &&
              ech_server_decrypt(&srv, &twisted, sk, pk, &cfg) == -3, "");
    return g_checks - before;
}

#endif /* ECH_PROTO_CHECK_IMPL_H */
