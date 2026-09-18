/*
 * ech_wire_impl.h —— ClientHello / ECHConfig / ECHClientHello 的编解码（RFC 9849 §4-§5）
 *
 * ClientHello（**不含** 4 字节握手头）必须逐字节忠实：ECH 的 AAD 就是这个结构的
 * 序列化结果（RFC 9849 §5.2），只做个「像 TLS」的近似会让 AAD 对不上。
 *
 * 结构用**内联定长数组**而不是指针，避免调用方还要管这些缓冲的生命周期；
 * 代价是体积不小（单个 ech_client_hello 约 3 KB），所以 ech_demo.c 里一律声明成 static。
 */
#ifndef ECH_WIRE_IMPL_H
#define ECH_WIRE_IMPL_H

#include "ech_buf_impl.h"

#define ECH_VERSION 0xFE0D
#define ECH_EXT_TYPE 0xFE0D
#define ECH_OUTER_EXTENSIONS 0xFD00
#define ECH_EXT_SERVER_NAME 0x0000
#define ECH_EXT_SUPPORTED_VERSIONS 0x002B
#define ECH_EXT_KEY_SHARE 0x0033

#define ECH_MAX_EXT 8
#define ECH_EXT_DATA 400
#define ECH_MAX_SUITES 8
#define ECH_MAX_SESSION 32

typedef struct {
    uint16_t type;
    size_t len;
    uint8_t data[ECH_EXT_DATA];
} ech_ext;

typedef struct {
    uint8_t random[32];
    uint8_t session_id[ECH_MAX_SESSION];
    size_t session_id_len;
    uint16_t cipher_suites[ECH_MAX_SUITES];
    size_t cipher_suites_len;
    uint8_t compression[8];
    size_t compression_len;
    ech_ext exts[ECH_MAX_EXT];
    size_t ext_count;
} ech_client_hello;
static int ch_encode(const ech_client_hello *ch, uint8_t *out, size_t cap,
                     size_t *out_len);
static int ch_decode(ech_client_hello *ch, const uint8_t *data, size_t len,
                     size_t *consumed);
static int ch_ext_index(const ech_client_hello *ch, uint16_t type);
static int ch_set_ext(ech_client_hello *ch, uint16_t type, const uint8_t *data,
                      size_t len);

/* ---------------------------------------------------------------- ClientHello */

static int ch_encode(const ech_client_hello *ch, uint8_t *out, size_t cap,
                     size_t *out_len)
{
    ech_buf b;
    size_t at;
    size_t i;
    buf_init(&b, out, cap);
    if (buf_u16(&b, 0x0303) != 0 || buf_put(&b, ch->random, 32) != 0 ||
        buf_u8(&b, (uint8_t)ch->session_id_len) != 0 ||
        buf_put(&b, ch->session_id, ch->session_id_len) != 0) {
        return -1;
    }
    at = buf_begin_vec16(&b);
    for (i = 0; i < ch->cipher_suites_len; i++) {
        if (buf_u16(&b, ch->cipher_suites[i]) != 0) {
            return -1;
        }
    }
    if (buf_fill_vec16(&b, at) != 0) {
        return -1;
    }
    if (buf_u8(&b, (uint8_t)ch->compression_len) != 0 ||
        buf_put(&b, ch->compression, ch->compression_len) != 0) {
        return -1;
    }
    at = buf_begin_vec16(&b);
    for (i = 0; i < ch->ext_count; i++) {
        if (buf_u16(&b, ch->exts[i].type) != 0 ||
            buf_u16(&b, (uint16_t)ch->exts[i].len) != 0 ||
            buf_put(&b, ch->exts[i].data, ch->exts[i].len) != 0) {
            return -1;
        }
    }
    if (buf_fill_vec16(&b, at) != 0) {
        return -1;
    }
    *out_len = b.len;
    return 0;
}

/* consumed 返回 ClientHello 自身的长度 —— EncodedClientHelloInner 后面还跟着零填充 */
static int ch_decode(ech_client_hello *ch, const uint8_t *data, size_t len,
                     size_t *consumed)
{
    ech_reader r, er;
    const uint8_t *p = NULL, *blob = NULL;
    size_t blob_len = 0, i;
    uint16_t v = 0;
    uint8_t u = 0;

    memset(ch, 0, sizeof(*ch));
    rd_init(&r, data, len);
    if (rd_u16(&r, &v) != 0 || v != 0x0303) {
        return -1;                       /* legacy_version 必须是 0x0303 */
    }
    if (rd_take(&r, 32, &p) != 0) {
        return -1;
    }
    memcpy(ch->random, p, 32);
    if (rd_vec8(&r, &blob, &blob_len) != 0 || blob_len > ECH_MAX_SESSION) {
        return -1;
    }
    memcpy(ch->session_id, blob, blob_len);
    ch->session_id_len = blob_len;

    if (rd_vec16(&r, &blob, &blob_len) != 0 || blob_len == 0 || (blob_len % 2) != 0) {
        return -1;
    }
    if (blob_len / 2 > ECH_MAX_SUITES) {
        return -1;
    }
    for (i = 0; i < blob_len / 2; i++) {
        ch->cipher_suites[i] = (uint16_t)(((uint16_t)blob[2 * i] << 8) | blob[2 * i + 1]);
    }
    ch->cipher_suites_len = blob_len / 2;

    if (rd_vec8(&r, &blob, &blob_len) != 0 || blob_len == 0 ||
        blob_len > sizeof(ch->compression)) {
        return -1;
    }
    memcpy(ch->compression, blob, blob_len);
    ch->compression_len = blob_len;

    if (rd_vec16(&r, &blob, &blob_len) != 0) {
        return -1;
    }
    rd_init(&er, blob, blob_len);
    while (er.pos < er.len) {
        ech_ext *e;
        if (ch->ext_count >= ECH_MAX_EXT) {
            return -1;
        }
        e = &ch->exts[ch->ext_count];
        if (rd_u16(&er, &e->type) != 0 || rd_vec16(&er, &p, &e->len) != 0 ||
            e->len > ECH_EXT_DATA) {
            return -1;
        }
        memcpy(e->data, p, e->len);
        for (i = 0; i < ch->ext_count; i++) {
            if (ch->exts[i].type == e->type) {
                return -1;               /* 扩展类型重复（RFC 8446 §4.2 禁止） */
            }
        }
        ch->ext_count++;
    }
    *consumed = rd_consumed(&r);
    return 0;
}

static int ch_ext_index(const ech_client_hello *ch, uint16_t type)
{
    size_t i;
    for (i = 0; i < ch->ext_count; i++) {
        if (ch->exts[i].type == type) {
            return (int)i;
        }
    }
    return -1;
}

/* 已存在则原地覆盖，否则追加到末尾（ECH 扩展必须最后追加，见 §6.1） */
static int ch_set_ext(ech_client_hello *ch, uint16_t type, const uint8_t *data,
                      size_t len)
{
    int at = ch_ext_index(ch, type);
    ech_ext *e;
    if (len > ECH_EXT_DATA) {
        return -1;
    }
    if (at < 0) {
        if (ch->ext_count >= ECH_MAX_EXT) {
            return -1;
        }
        at = (int)ch->ext_count++;
        ch->exts[at].type = type;
    }
    e = &ch->exts[at];
    e->len = len;
    memcpy(e->data, data, len);
    return 0;
}

/* ---------------------------------------------------------------- ECHClientHello（§5） */

static int ech_outer_ext_encode(uint16_t kdf_id, uint16_t aead_id, uint8_t config_id,
                                const uint8_t *enc, size_t enc_len,
                                const uint8_t *payload, size_t payload_len,
                                uint8_t *out, size_t cap, size_t *out_len)
{
    ech_buf b;
    buf_init(&b, out, cap);
    if (buf_u8(&b, 0x00) != 0 ||          /* ECHClientHelloType = outer(0) */
        buf_u16(&b, kdf_id) != 0 || buf_u16(&b, aead_id) != 0 ||
        buf_u8(&b, config_id) != 0 ||
        buf_u16(&b, (uint16_t)enc_len) != 0 || buf_put(&b, enc, enc_len) != 0 ||
        buf_u16(&b, (uint16_t)payload_len) != 0 ||
        buf_put(&b, payload, payload_len) != 0) {
        return -1;
    }
    *out_len = b.len;
    return 0;
}

static int ech_outer_ext_decode(const uint8_t *data, size_t len, uint16_t *kdf_id,
                               uint16_t *aead_id, uint8_t *config_id,
                               const uint8_t **enc, size_t *enc_len,
                               const uint8_t **payload, size_t *payload_len)
{
    ech_reader r;
    uint8_t type = 0;
    rd_init(&r, data, len);
    if (rd_u8(&r, &type) != 0 || type != 0x00) {
        return -1;                       /* 不是 outer 变体 */
    }
    if (rd_u16(&r, kdf_id) != 0 || rd_u16(&r, aead_id) != 0 ||
        rd_u8(&r, config_id) != 0 || rd_vec16(&r, enc, enc_len) != 0 ||
        rd_vec16(&r, payload, payload_len) != 0 || rd_end(&r) != 0) {
        return -1;
    }
    return 0;
}

#endif /* ECH_WIRE_IMPL_H */