/*
 * ech_cmp_impl.h —— 结构比对与杂项工具
 *
 * 这些函数都不属于协议实现本身，只在自检里用，所以单独放一个头：
 * 实现头（ech_wire/hpke/inner/flow）要能和自检完全分离，读者才分得清
 * 哪一段是「协议必须这样做」、哪一段只是「测试为了方便」。
 */
#ifndef ECH_CMP_IMPL_H
#define ECH_CMP_IMPL_H

#include "ech_check_impl.h"

/* 逐字段比对两个 ClientHello：扩展的**顺序**也算内容（§5.1 要求压缩后能还原顺序） */
static int same_ch(const ech_client_hello *a, const ech_client_hello *b)
{
    size_t i;
    if (a->session_id_len != b->session_id_len ||
        a->cipher_suites_len != b->cipher_suites_len ||
        a->compression_len != b->compression_len || a->ext_count != b->ext_count ||
        memcmp(a->random, b->random, 32) != 0 ||
        memcmp(a->session_id, b->session_id, a->session_id_len) != 0 ||
        memcmp(a->cipher_suites, b->cipher_suites, a->cipher_suites_len * 2) != 0 ||
        memcmp(a->compression, b->compression, a->compression_len) != 0) {
        return 0;
    }
    for (i = 0; i < a->ext_count; i++) {
        if (a->exts[i].type != b->exts[i].type || a->exts[i].len != b->exts[i].len ||
            memcmp(a->exts[i].data, b->exts[i].data, a->exts[i].len) != 0) {
            return 0;
        }
    }
    return 1;
}

static int has_sub(const uint8_t *buf, size_t len, const char *needle)
{
    size_t n = strlen(needle), i;
    if (n == 0 || n > len) {
        return 0;
    }
    for (i = 0; i + n <= len; i++) {
        if (memcmp(buf + i, needle, n) == 0) {
            return 1;
        }
    }
    return 0;
}

/* 保持 (cipher_suite, config_id, enc) 不变，只换 ECH 扩展的 payload */
static int t_rebuild_ech_ext(ech_client_hello *ch, const uint8_t *payload,
                            size_t payload_len)
{
    uint8_t extbuf[ECH_EXT_DATA];
    const uint8_t *enc = NULL, *old = NULL;
    size_t enc_len = 0, old_len = 0, n = 0;
    uint16_t kdf_id = 0, aead_id = 0;
    uint8_t cid = 0;
    int at = ch_ext_index(ch, ECH_EXT_TYPE);
    if (at < 0 ||
        ech_outer_ext_decode(ch->exts[at].data, ch->exts[at].len, &kdf_id, &aead_id, &cid,
                             &enc, &enc_len, &old, &old_len) != 0 ||
        ech_outer_ext_encode(kdf_id, aead_id, cid, enc, enc_len, payload, payload_len,
                             extbuf, sizeof(extbuf), &n) != 0) {
        return -1;
    }
    return ch_set_ext(ch, ECH_EXT_TYPE, extbuf, n);
}

/* 两段字节拼接后的 SHA-256：ECH 的 transcript 就是把握手消息顺序喂进 Hash */
static int t_sha256_2(const uint8_t *a, size_t a_len, const uint8_t *b, size_t b_len,
                      uint8_t *out)
{
    static uint8_t buf[512];
    if (a_len + b_len > sizeof(buf)) {
        return -1;
    }
    memcpy(buf, a, a_len);
    memcpy(buf + a_len, b, b_len);
    return SHA256(buf, a_len + b_len, out) != NULL ? 0 : -1;
}

#endif /* ECH_CMP_IMPL_H */
