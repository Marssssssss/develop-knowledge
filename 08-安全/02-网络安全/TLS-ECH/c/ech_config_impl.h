/*
 * ech_config_impl.h —— ECHConfig / ECHConfigList 的编解码（RFC 9849 §4.1-§4.2）
 *
 * ECHConfig 是服务端通过 HTTPS DNS 记录（或 DNS 里的 ech 参数）发布会话密钥的部分。
 * 注意 **KEM 的公钥与套件列表都带长度前缀**，而 maximum_name_length / public_name
 * 是 u8 前缀 —— 少一层的写法在本地自测里完全正常，只有和真实配置对拍才会暴露。
 */
#ifndef ECH_CONFIG_IMPL_H
#define ECH_CONFIG_IMPL_H

#include "ech_wire_impl.h"

#define ECH_MAX_PUBLIC_NAME 64

typedef struct {
    uint8_t config_id;
    uint16_t kem_id;
    uint16_t kdf_id;
    uint16_t aead_id;
    uint8_t public_key[64];
    size_t public_key_len;
    uint8_t maximum_name_length;
    uint8_t public_name[ECH_MAX_PUBLIC_NAME];
    size_t public_name_len;
} ech_config;

/* ---------------------------------------------------------------- ECHConfig */

static int ech_config_encode(const ech_config *cfg, uint8_t *out, size_t cap,
                             size_t *out_len)
{
    ech_buf b;
    size_t at, inner;
    buf_init(&b, out, cap);
    if (buf_u16(&b, ECH_VERSION) != 0) {
        return -1;
    }
    at = buf_begin_vec16(&b);                       /* ECHConfig.length */
    if (buf_u8(&b, cfg->config_id) != 0 || buf_u16(&b, cfg->kem_id) != 0 ||
        buf_u16(&b, (uint16_t)cfg->public_key_len) != 0 ||
        buf_put(&b, cfg->public_key, cfg->public_key_len) != 0) {
        return -1;
    }
    inner = buf_begin_vec16(&b);                    /* cipher_suites */
    if (buf_u16(&b, cfg->kdf_id) != 0 || buf_u16(&b, cfg->aead_id) != 0 ||
        buf_fill_vec16(&b, inner) != 0) {
        return -1;
    }
    if (buf_u8(&b, cfg->maximum_name_length) != 0 ||
        buf_u8(&b, (uint8_t)cfg->public_name_len) != 0 ||
        buf_put(&b, cfg->public_name, cfg->public_name_len) != 0) {
        return -1;
    }
    inner = buf_begin_vec16(&b);                    /* extensions：本 demo 恒空 */
    if (buf_fill_vec16(&b, inner) != 0 || buf_fill_vec16(&b, at) != 0) {
        return -1;
    }
    *out_len = b.len;
    return 0;
}

static int ech_config_decode(ech_config *cfg, const uint8_t *data, size_t len)
{
    ech_reader r, body, sr;
    const uint8_t *p = NULL;
    size_t n = 0;
    uint16_t version = 0, suites_len = 0;

    memset(cfg, 0, sizeof(*cfg));
    rd_init(&r, data, len);
    if (rd_u16(&r, &version) != 0 || version != ECH_VERSION) {
        return -1;                       /* 不认识的版本由调用方靠 length 跳过 */
    }
    if (rd_vec16(&r, &p, &n) != 0 || rd_end(&r) != 0) {
        return -1;
    }
    rd_init(&body, p, n);

    if (rd_u8(&body, &cfg->config_id) != 0 || rd_u16(&body, &cfg->kem_id) != 0) {
        return -1;
    }
    if (rd_vec16(&body, &p, &n) != 0 || n == 0 || n > sizeof(cfg->public_key)) {
        return -1;
    }
    memcpy(cfg->public_key, p, n);
    cfg->public_key_len = n;

    if (rd_vec16(&body, &p, &suites_len) != 0 || suites_len < 4 ||
        (suites_len % 4) != 0) {
        return -1;
    }
    rd_init(&sr, p, suites_len);
    if (rd_u16(&sr, &cfg->kdf_id) != 0 || rd_u16(&sr, &cfg->aead_id) != 0) {
        return -1;
    }
    if (rd_u8(&body, &cfg->maximum_name_length) != 0) {
        return -1;
    }
    if (rd_vec8(&body, &p, &n) != 0 || n == 0 || n > sizeof(cfg->public_name)) {
        return -1;
    }
    memcpy(cfg->public_name, p, n);
    cfg->public_name_len = n;
    if (rd_vec16(&body, &p, &n) != 0 || rd_end(&body) != 0) {
        return -1;
    }
    return n == 0 ? 0 : -1;              /* 本 demo 只支持无扩展的 ECHConfig */
}

#endif /* ECH_CONFIG_IMPL_H */