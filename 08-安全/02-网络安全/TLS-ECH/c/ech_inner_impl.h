/*
 * ech_inner_impl.h —— EncodedClientHelloInner 的构造与还原（RFC 9849 §5.1、§6.1.3）
 *
 * EncodedClientHelloInner = ClientHelloInner（legacy_session_id 清空）| 全零填充。
 * 填充的目的不是隐蔽长度，而是**把长度对齐到 32 字节**，让被动观察者无法从长度
 * 区分不同 SNI；有 SNI 时补 max(0, M - D)（M = maximum_name_length、D = SNI 长度），
 * 无 SNI 时补 M + 9，最后再补到 32 的倍数。
 *
 * ech_outer_extensions(0xfd00) 压缩：内层里与 ClientHelloOuter 逐字节相同的扩展
 * 不重复发送，改成一个类型列表；服务端按列表从 outer 里搬回来。这里集中了 §5.1 的
 * 四条 MUST abort —— 少了任何一条，服务端就成了放大攻击的反射器。
 */
#ifndef ECH_INNER_IMPL_H
#define ECH_INNER_IMPL_H

#include "ech_wire_impl.h"

#define ECH_SCRATCH 1024

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

/* ---------------------------------------------------------------- 填充（§6.1.3） */

static size_t ech_name_padding(const ech_client_hello *inner, uint8_t max_name, int *ok)
{
    int at = ch_ext_index(inner, ECH_EXT_SERVER_NAME);
    ech_reader r, sr;
    const uint8_t *p = NULL;
    size_t n = 0, name_len = 0;
    uint8_t name_type = 0;
    *ok = 1;
    if (at < 0) {
        return (size_t)max_name + 9;      /* 没有 SNI：补 M + 9 */
    }
    rd_init(&r, inner->exts[at].data, inner->exts[at].len);
    if (rd_vec16(&r, &p, &n) != 0 || rd_end(&r) != 0) {
        *ok = 0;
        return 0;
    }
    rd_init(&sr, p, n);
    if (rd_u8(&sr, &name_type) != 0 || name_type != 0) {
        *ok = 0;                          /* name_type 必须是 host_name(0) */
        return 0;
    }
    if (rd_vec16(&sr, &p, &name_len) != 0 || rd_end(&sr) != 0) {
        *ok = 0;
        return 0;
    }
    return name_len < (size_t)max_name ? (size_t)max_name - name_len : 0;
}

static size_t ech_round_to_32(size_t length)
{
    return 31 - ((length - 1) % 32);      /* 返回**要补**的字节数 N */
}

/* ClientHelloInner 清空 legacy_session_id（§5.1）后追加全零填充 */
static int ech_encoded_inner(const ech_client_hello *inner, size_t padding, uint8_t *out,
                             size_t cap, size_t *out_len)
{
    ech_client_hello cleared = *inner;
    size_t n = 0;
    cleared.session_id_len = 0;
    if (ch_encode(&cleared, out, cap, &n) != 0 || n + padding > cap) {
        return -1;
    }
    memset(out + n, 0, padding);
    *out_len = n + padding;
    return 0;
}

/* ---------------------------------------------------------------- 扩展压缩（§5.1） */

static int ech_compress_inner(ech_client_hello *dst, const ech_client_hello *inner,
                              const ech_client_hello *outer, const uint16_t *types,
                              size_t n_types)
{
    uint16_t keep[ECH_MAX_EXT];
    uint8_t marker[2 * ECH_MAX_EXT];
    size_t n_keep = 0, marker_len = 0, first = (size_t)-1, i, j;

    *dst = *inner;
    for (i = 0; i < n_types && n_keep < ECH_MAX_EXT; i++) {
        int a = ch_ext_index(inner, types[i]);
        int b = ch_ext_index(outer, types[i]);
        if (a < 0 || b < 0 || inner->exts[a].len != outer->exts[b].len) {
            continue;
        }
        if (inner->exts[a].len > 0 &&
            memcmp(inner->exts[a].data, outer->exts[b].data, inner->exts[a].len) != 0) {
            continue;                     /* 内容不同就不能省 */
        }
        keep[n_keep] = types[i];
        if ((size_t)a < first) {
            first = (size_t)a;
        }
        marker[marker_len++] = (uint8_t)(types[i] >> 8);
        marker[marker_len++] = (uint8_t)(types[i] & 0xff);
        n_keep++;
    }
    if (n_keep == 0) {
        return 0;
    }
    dst->ext_count = 0;
    for (i = 0; i < inner->ext_count; i++) {
        int is_kept = 0;
        for (j = 0; j < n_keep; j++) {
            if (inner->exts[i].type == keep[j]) {
                is_kept = 1;
                break;
            }
        }
        if (is_kept) {
            if (i == first &&
                ch_set_ext(dst, ECH_OUTER_EXTENSIONS, marker, marker_len) != 0) {
                return -1;
            }
            continue;
        }
        if (ch_set_ext(dst, inner->exts[i].type, inner->exts[i].data,
                       inner->exts[i].len) != 0) {
            return -1;
        }
    }
    return 0;
}

static int ech_decompress_inner(ech_client_hello *dst, const uint8_t *encoded, size_t n,
                                const ech_client_hello *outer)
{
    ech_client_hello inner, out;
    uint16_t order[ECH_MAX_EXT];
    size_t n_order = 0, i, j;
    size_t consumed = 0;

    if (ch_decode(&inner, encoded, n, &consumed) != 0) {
        return -1;
    }
    if (ch_ext_index(&inner, ECH_OUTER_EXTENSIONS) < 0) {
        *dst = inner;
        return 0;
    }
    {
        int at = ch_ext_index(&inner, ECH_OUTER_EXTENSIONS);
        if (inner.exts[at].len < 2 || (inner.exts[at].len % 2) != 0 ||
            inner.exts[at].len / 2 > ECH_MAX_EXT) {
            return -1;
        }
        n_order = inner.exts[at].len / 2;
        for (i = 0; i < n_order; i++) {
            order[i] = (uint16_t)(((uint16_t)inner.exts[at].data[2 * i] << 8) |
                                  inner.exts[at].data[2 * i + 1]);
            for (j = 0; j < i; j++) {
                if (order[j] == order[i]) {
                    return -5;            /* abort 2：重复引用 */
                }
            }
            if (order[i] == ECH_EXT_TYPE) {
                return -6;                /* abort 3：引用了 encrypted_client_hello */
            }
            if (ch_ext_index(outer, order[i]) < 0) {
                return -4;                /* abort 1：outer 里没有这个扩展 */
            }
        }
        for (i = 1; i < n_order; i++) {
            if (ch_ext_index(outer, order[i - 1]) >= ch_ext_index(outer, order[i])) {
                return -7;                /* abort 4：相对顺序不对（防放大攻击） */
            }
        }
    }
    out = inner;
    out.ext_count = 0;
    for (i = 0; i < inner.ext_count; i++) {
        if (inner.exts[i].type != ECH_OUTER_EXTENSIONS) {
            if (ch_set_ext(&out, inner.exts[i].type, inner.exts[i].data,
                           inner.exts[i].len) != 0) {
                return -1;
            }
            continue;
        }
        for (j = 0; j < n_order; j++) {
            int b = ch_ext_index(outer, order[j]);
            if (ch_set_ext(&out, order[j], outer->exts[b].data, outer->exts[b].len) != 0) {
                return -1;
            }
        }
    }
    *dst = out;
    return 0;
}

#endif /* ECH_INNER_IMPL_H */