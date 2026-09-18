/*
 * ech_codec_check_impl.h —— B 编解码 与 C 填充公式
 *
 * 编解码这一层解析的是「对端发来的字节」与「DNS 里拉来的 ECHConfig」，越界读就是
 * 可得的内存安全问题，所以每个负例都要真的构造出来：尾部多余字节、重复扩展、
 * 带未知强制扩展的配置。填充公式则完全由 §6.1.3 的三条式子决定，可以逐条验证。
 */
#ifndef ECH_CODEC_CHECK_IMPL_H
#define ECH_CODEC_CHECK_IMPL_H

#include "ech_vectors_impl.h"

static int check_codec(void)
{
    static ech_config cfg, back;
    static ech_client_hello ch, dup, dec;
    static uint8_t a[512], b[512], pk[32], rnd[32], sid[32];
    size_t an = 0, bn = 0, consumed = 0;
    int before = g_checks;

    unhex(VEC_PK_R, pk, 32);
    t_make_config(&cfg, T_CONFIG_ID, pk);
    check("ECHConfig 以 version(0xfe0d) 开头",
          ech_config_encode(&cfg, a, sizeof(a), &an) == 0 && an > 8 && a[0] == 0xFE &&
              a[1] == 0x0D, "");
    check("ECHConfig.length 覆盖 contents（不含 version 与 length 自己）",
          ((((size_t)a[2] << 8) | a[3])) == an - 4, "");
    check("ECHConfig 解码后再编码逐字节相同",
          ech_config_decode(&back, a, an) == 0 &&
              ech_config_encode(&back, b, sizeof(b), &bn) == 0 && bn == an &&
              memcmp(a, b, an) == 0, "");
    check("public_name / maximum_name_length 还原正确",
          back.public_name_len == strlen(T_PUBLIC_NAME) &&
              memcmp(back.public_name, T_PUBLIC_NAME, back.public_name_len) == 0 &&
              back.maximum_name_length == T_MAX_NAME, "");
    check("密钥配置四元组还原正确",
          back.config_id == T_CONFIG_ID && back.kem_id == ECH_KEM_ID_X25519 &&
              back.kdf_id == ECH_KDF_ID_HKDF_SHA256 &&
              back.aead_id == ECH_AEAD_ID_CHACHA20POLY1305 && back.public_key_len == 32 &&
              memcmp(back.public_key, pk, 32) == 0, "");
    a[an] = 0x00;
    check("ECHConfig 尾部多一字节必须报错", ech_config_decode(&back, a, an + 1) != 0, "");

    /* 本实现只支持无扩展的 ECHConfig：往编码结果尾部接一个「未知且强制」（type 高位
     * 为 1）的扩展，整条配置必须被拒绝，而**不是**被静默忽略（RFC 9849 §4.1） */
    ech_config_encode(&cfg, b, sizeof(b), &bn);
    b[bn - 2] = 0x00;
    b[bn - 1] = 0x04;                             /* extensions<0..2^16-1> = 4 字节 */
    b[bn] = 0x80;
    b[bn + 1] = 0x01;
    b[bn + 2] = 0x00;
    b[bn + 3] = 0x00;
    b[2] = (uint8_t)(bn >> 8);                    /* contents 变长 4 字节（= bn） */
    b[3] = (uint8_t)(bn & 0xff);
    check("带未知强制扩展(0x8001)的 ECHConfig 必须整条拒绝",
          ech_config_decode(&back, b, bn + 4) != 0, "");

    t_fill(rnd, 32, 0x11);
    t_fill(sid, 32, 0x22);
    t_make_inner(&ch, rnd, sid, 32);
    check("ClientHello 编码成功", ch_encode(&ch, a, sizeof(a), &an) == 0, "");
    check("ClientHello 编解码往返（consumed == 总长）",
          ch_decode(&dec, a, an, &consumed) == 0 && consumed == an, "");
    check("往返后逐字段一致（含扩展顺序）", same_ch(&ch, &dec), "");
    /* 这正是 EncodedClientHelloInner 尾部零填充所依赖的行为：解码器只吃到 ClientHello
     * 的边界，剩下的字节留给调用方判定是否全零 —— 所以这里**不**要求吃光 */
    a[an] = 0x00;
    check("尾部多字节时 consumed 只吃 ClientHello 本体",
          ch_decode(&dec, a, an + 1, &consumed) == 0 && consumed == an, "");

    dup = ch;
    dup.ext_count = 2;
    dup.exts[0].type = ECH_EXT_SUPPORTED_VERSIONS;
    dup.exts[0].len = 3;
    memcpy(dup.exts[0].data, "\x02\x03\x04", 3);
    dup.exts[1] = dup.exts[0];
    check("重复扩展必须报错（RFC 8446 §4.2）",
          ch_encode(&dup, b, sizeof(b), &bn) == 0 &&
              ch_decode(&dec, b, bn, &consumed) != 0, "");
    return g_checks - before;
}

static int check_padding(void)
{
    static ech_client_hello inner, no_sni, bad, dec;
    static uint8_t rnd[32], sid[32], body[512];
    const size_t lens[9] = {1, 2, 31, 32, 33, 63, 64, 65, 100};
    size_t n = 0, consumed = 0, sni_len = strlen(T_SNI), j;
    int before = g_checks, ok = 0, all_ok = 1;

    t_fill(rnd, 32, 0x33);
    t_fill(sid, 32, 0x44);
    t_make_inner(&inner, rnd, sid, 32);

    ok = 0;
    check("有 SNI：补 max(0, M - D)",
          ech_name_padding(&inner, T_MAX_NAME, &ok) == T_MAX_NAME - sni_len && ok == 1, "");
    ok = 0;
    check("M == D：补 0", ech_name_padding(&inner, (uint8_t)sni_len, &ok) == 0 && ok == 1, "");
    ok = 0;
    check("M < D：补 0 而不是负数",
          ech_name_padding(&inner, (uint8_t)(sni_len - 1), &ok) == 0 && ok == 1, "");

    /* 没有 SNI 时补 M + 9：一个 M 字节 host_name 的 server_name 扩展长度 */
    no_sni = inner;
    no_sni.exts[0] = inner.exts[2];               /* 只留 key_share，去掉 server_name */
    no_sni.ext_count = 1;
    ok = 0;
    check("无 SNI：补 M + 9",
          ech_name_padding(&no_sni, T_MAX_NAME, &ok) == (size_t)T_MAX_NAME + 9 && ok == 1, "");

    /* server_name 的 extension_data 是 ServerNameList，要解两层才到 name_type：
     * data[0..1] 是列表长度，data[2] 才是 name_type */
    bad = inner;
    bad.exts[0].data[2] = 0x01;
    ok = 1;
    check("name_type 不是 host_name 必须报错",
          ech_name_padding(&bad, T_MAX_NAME, &ok) == 0 && ok == 0, "");

    for (j = 0; j < 9; j++) {
        size_t total = lens[j] + ech_round_to_32(lens[j]);
        if (total % 32 != 0 || total < lens[j] || total - lens[j] >= 32) {
            all_ok = 0;
        }
    }
    check("round_to_32：补齐到 32 的倍数且补得最少", all_ok, "");

    check("EncodedClientHelloInner 清空 legacy_session_id 再补零",
          ech_encoded_inner(&inner, 7, body, sizeof(body), &n) == 0 && n > 7 &&
              memcmp(body + n - 7, "\x00\x00\x00\x00\x00\x00\x00", 7) == 0, "");
    check("去掉尾部填充后可解码且 session_id 为空",
          ch_decode(&dec, body, n - 7, &consumed) == 0 && dec.session_id_len == 0 &&
              consumed == n - 7, "");
    return g_checks - before;
}

#endif /* ECH_CODEC_CHECK_IMPL_H */
