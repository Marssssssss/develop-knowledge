/*
 * ech_flow_impl.h —— ECH 的客户端 / 服务端流程（RFC 9849 §5-§7）
 *
 * 客户端（§6.1）：先按密文长度填等长零的占位 payload 算出 ClientHelloOuterAAD，
 * 再 final_payload = HPKE.Seal(AAD, EncodedClientHelloInner)，最后把占位换掉 ——
 * 两段等长，所以不用重算任何长度前缀。
 *
 * 服务端（§7.1）：把 payload 换成等长零重建 AAD → Open → 校验尾部填充全零 →
 * 展开 ech_outer_extensions → legacy_session_id 从 ClientHelloOuter 抄回来。
 *
 * 返回值约定（自检要按原因分辨，所以不用统一的 -1）：
 *   0  成功
 *  -1  解析 / AAD / 认证失败
 *  -2  config_id 或套件不匹配（试解密失败，服务端应换下一个 ECHConfig）
 *  -3  填充里出现非零字节
 *  -4 / -5 / -6 / -7  ech_outer_extensions 的四条 MUST abort（§5.1）
 */
#ifndef ECH_FLOW_IMPL_H
#define ECH_FLOW_IMPL_H

#include "ech_hpke_ctx_impl.h"
#include "ech_wire_impl.h"
#include "ech_config_impl.h"
#include "ech_inner_impl.h"


static size_t ech_name_padding(const ech_client_hello *inner, uint8_t max_name, int *ok);
static size_t ech_round_to_32(size_t length);
static int ech_encoded_inner(const ech_client_hello *inner, size_t padding, uint8_t *out,
                             size_t cap, size_t *out_len);
static int ech_compress_inner(ech_client_hello *dst, const ech_client_hello *inner,
                              const ech_client_hello *outer, const uint16_t *types,
                              size_t n_types);
static int ech_decompress_inner(ech_client_hello *dst, const uint8_t *encoded, size_t n,
                                const ech_client_hello *outer);
static int ech_client_encrypt(ech_client_hello *outer_out, ech_context *ctx,
                              uint8_t *encoded, size_t *encoded_len, size_t encoded_cap,
                              const ech_client_hello *inner,
                              const ech_client_hello *outer_tpl, const ech_config *cfg,
                              const uint16_t *compress_types, size_t n_compress);
static int ech_server_decrypt(ech_client_hello *inner_out, const ech_client_hello *outer,
                              const uint8_t sk_r[32], const uint8_t pk_r[32],
                              const ech_config *cfg);
static int ech_accept_confirmation(const uint8_t inner_random[32], const uint8_t *transcript,
                                   size_t transcript_len, uint8_t out[8]);

/* ---------------------------------------------------------------- 客户端（§6.1） */

static int ech_client_encrypt(ech_client_hello *outer_out, ech_context *ctx,
                              uint8_t *encoded, size_t *encoded_len, size_t encoded_cap,
                              const ech_client_hello *inner,
                              const ech_client_hello *outer_tpl, const ech_config *cfg,
                              const uint16_t *compress_types, size_t n_compress)
{
    ech_client_hello squeezed, zero;
    uint8_t enc[32], ss[ECH_NSECRET], zerobuf[ECH_SCRATCH];
    uint8_t extbuf[ECH_EXT_DATA], aad[2048], sealed[ECH_SCRATCH];
    size_t n = 0, ext_len = 0, aad_len = 0, filler;
    size_t pad;
    int ok = 0, sealed_len, rc = -1;

    if (cfg->kem_id != ECH_KEM_ID_X25519 || cfg->kdf_id != ECH_KDF_ID_HKDF_SHA256 ||
        cfg->aead_id != ECH_AEAD_ID_CHACHA20POLY1305) {
        return -1;                        /* ECHConfig 不支持所选套件 */
    }
    if (ech_compress_inner(&squeezed, inner, outer_tpl, compress_types, n_compress) != 0) {
        return -1;
    }
    pad = ech_name_padding(inner, cfg->maximum_name_length, &ok);
    if (!ok || ech_encoded_inner(&squeezed, pad, encoded, encoded_cap, &n) != 0) {
        return -1;
    }
    n += ech_round_to_32(n);
    if (n > encoded_cap) {
        return -1;
    }
    *encoded_len = n;

    if (ech_encap(cfg->public_key, NULL, enc, ss) != 0 ||
        ech_key_schedule(ss, NULL, 0, ctx) != 0) {
        goto done;
    }

    /* 占位：与密文等长的零，写进 AAD 用的那份 ClientHelloOuter */
    filler = n + ECH_TAG_LEN;
    if (filler > sizeof(zerobuf)) {
        goto done;
    }
    memset(zerobuf, 0, filler);
    zero = *outer_tpl;
    if (ech_outer_ext_encode(cfg->kdf_id, cfg->aead_id, cfg->config_id, enc, 32, zerobuf,
                             filler, extbuf, sizeof(extbuf), &ext_len) != 0 ||
        ch_set_ext(&zero, ECH_EXT_TYPE, extbuf, ext_len) != 0 ||
        ch_encode(&zero, aad, sizeof(aad), &aad_len) != 0) {
        goto done;
    }
    sealed_len = ech_ctx_seal(ctx, aad, aad_len, encoded, n, sealed);
    if (sealed_len != (int)filler) {
        goto done;
    }
    *outer_out = *outer_tpl;
    if (ech_outer_ext_encode(cfg->kdf_id, cfg->aead_id, cfg->config_id, enc, 32, sealed,
                             (size_t)sealed_len, extbuf, sizeof(extbuf), &ext_len) != 0 ||
        ch_set_ext(outer_out, ECH_EXT_TYPE, extbuf, ext_len) != 0) {
        goto done;
    }
    rc = 0;
done:
    OPENSSL_cleanse(ss, sizeof(ss));
    return rc;
}

/* ---------------------------------------------------------------- 服务端（§7.1） */

static int ech_server_decrypt(ech_client_hello *inner_out, const ech_client_hello *outer,
                              const uint8_t sk_r[32], const uint8_t pk_r[32],
                              const ech_config *cfg)
{
    ech_client_hello aad_ch, parsed;
    ech_context ctx;
    uint8_t ss[ECH_NSECRET], plain[ECH_SCRATCH], zerobuf[ECH_SCRATCH];
    uint8_t extbuf[ECH_EXT_DATA], aad[2048];
    const uint8_t *enc = NULL, *payload = NULL;
    size_t enc_len = 0, payload_len = 0, aad_len = 0, consumed = 0, i;
    uint16_t kdf_id = 0, aead_id = 0;
    uint8_t config_id = 0;
    int at = ch_ext_index(outer, ECH_EXT_TYPE);
    int n, rc;

    if (at < 0) {
        return -1;
    }
    if (ech_outer_ext_decode(outer->exts[at].data, outer->exts[at].len, &kdf_id, &aead_id,
                             &config_id, &enc, &enc_len, &payload, &payload_len) != 0) {
        return -1;
    }
    if (config_id != cfg->config_id) {
        return -2;                        /* 试解密失败：config_id 不对 */
    }
    if (kdf_id != ECH_KDF_ID_HKDF_SHA256 || aead_id != ECH_AEAD_ID_CHACHA20POLY1305 ||
        enc_len != 32 || payload_len < ECH_TAG_LEN ||
        payload_len > sizeof(plain) + ECH_TAG_LEN) {
        return -2;
    }
    if (ech_decap(enc, sk_r, pk_r, ss) != 0 ||
        ech_key_schedule(ss, NULL, 0, &ctx) != 0) {
        return -1;
    }
    OPENSSL_cleanse(ss, sizeof(ss));

    /* 重建 AAD：payload 换成等长的零，长度前缀一个字节都不动 */
    aad_ch = *outer;
    memset(zerobuf, 0, payload_len);
    {
        size_t ext_len = 0;
        if (ech_outer_ext_encode(kdf_id, aead_id, config_id, enc, enc_len, zerobuf,
                                payload_len, extbuf, sizeof(extbuf), &ext_len) != 0 ||
            ch_set_ext(&aad_ch, ECH_EXT_TYPE, extbuf, ext_len) != 0 ||
            ch_encode(&aad_ch, aad, sizeof(aad), &aad_len) != 0) {
            return -1;
        }
    }
    n = ech_ctx_open(&ctx, aad, aad_len, payload, payload_len, plain);
    if (n < 0) {
        return -1;                        /* 认证失败 = ClientHelloOuter 被改过 */
    }
    if (ch_decode(&parsed, plain, (size_t)n, &consumed) != 0) {
        return -1;
    }
    for (i = consumed; i < (size_t)n; i++) {
        if (plain[i] != 0) {
            return -3;                    /* §5.1：填充必须全零 */
        }
    }
    rc = ech_decompress_inner(inner_out, plain, consumed, outer);
    if (rc != 0) {
        return rc;
    }
    if (ch_ext_index(inner_out, ECH_EXT_TYPE) < 0) {
        return -1;
    }
    memcpy(inner_out->session_id, outer->session_id, outer->session_id_len);
    inner_out->session_id_len = outer->session_id_len;
    return 0;
}

/* ---------------------------------------------------------------- 接受确认（§7.2） */

static int ech_accept_confirmation(const uint8_t inner_random[32], const uint8_t *transcript,
                                   size_t transcript_len, uint8_t out[8])
{
    uint8_t prk[ECH_NH];
    if (ech_extract(NULL, 0, inner_random, 32, prk) != 0) {
        return -1;
    }
    return ech_tls_expand_label(prk, "ech accept confirmation", transcript, transcript_len,
                                out, 8);
}

static int ech_hrr_accept_confirmation(const uint8_t inner_random[32],
                                       const uint8_t *transcript,
                                       size_t transcript_len, uint8_t out[8])
{
    uint8_t prk[ECH_NH];
    if (ech_extract(NULL, 0, inner_random, 32, prk) != 0) {
        return -1;
    }
    return ech_tls_expand_label(prk, "hrr ech accept confirmation", transcript,
                                transcript_len, out, 8);
}

#endif /* ECH_FLOW_IMPL_H */