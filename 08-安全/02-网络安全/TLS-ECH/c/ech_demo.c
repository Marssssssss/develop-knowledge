/*
 * ech_demo.c —— TLS 1.3 ECH（Encrypted Client Hello, RFC 9849）自检主程序
 *
 * 用例组：
 *   A  RFC 9180 附录 A.2 官方向量          ech_vectors_impl.h
 *   B  ClientHello / ECHConfig 编解码      ┐
 *   C  填充公式与 32 字节对齐              ┘ ech_codec_check_impl.h
 *   D  端到端往返                          ┐
 *   E  篡改与错密钥必然失败                ┘ ech_proto_check_impl.h
 *   F  ech_outer_extensions 压缩与四条 abort ┐
 *   G  接受确认值                            ┘ 本文件
 *
 * 编译：cc -O2 -Wall -Wextra -o ech_demo ech_demo.c -lcrypto
 * 运行：./ech_demo       （成功退出码 0）
 *
 * 本目录有 12 个头文件但只有 1 个 .c —— 头被文本级 #include（同一翻译单元、static
 * 可见性零变化、构建命令只编译 .c）。要单独跑某一组，改 main 里的 groups 即可。
 */
#include "ech_proto_check_impl.h"

/* ---------------------------------------------------------------- F 压缩与四条 abort */

static int check_outer_ext(void)
{
    static ech_config cfg;
    static ech_client_hello inner, tpl, squeezed, bad, srv;
    static ech_context ctx;
    static const uint16_t names[2] = {ECH_EXT_SUPPORTED_VERSIONS, ECH_EXT_KEY_SHARE};
    static uint8_t marker[8], body[1024], plain[ECH_SCRATCH], shrunk[ECH_SCRATCH];
    static uint8_t pk[32], sk[32], rnd[32], sid[32];
    size_t plain_len = 0, shrunk_len = 0, n = 0;
    int before = g_checks, at;

    unhex(VEC_PK_R, pk, 32);
    unhex(VEC_SK_R, sk, 32);
    t_make_config(&cfg, T_CONFIG_ID, pk);
    t_fill(rnd, 32, 0x88);
    t_fill(sid, 32, 0x99);
    t_make_inner(&inner, rnd, sid, 32);
    t_make_outer_tpl(&tpl, rnd, sid, 32);

    check("压缩成功", ech_compress_inner(&squeezed, &inner, &tpl, names, 2) == 0, "");
    check("ech_outer_extensions 落在被删的第一个扩展的位置",
          ch_ext_index(&squeezed, ECH_OUTER_EXTENSIONS) == 1, "");
    check("与 outer 逐字节相同的扩展被省掉（supported_versions / key_share）",
          ch_ext_index(&squeezed, ECH_EXT_SUPPORTED_VERSIONS) < 0 &&
              ch_ext_index(&squeezed, ECH_EXT_KEY_SHARE) < 0, "");
    check("不参与压缩的 server_name 必须保留",
          ch_ext_index(&squeezed, ECH_EXT_SERVER_NAME) >= 0, "");
    check("（前置）压缩后的编码成功",
          ch_encode(&squeezed, body, sizeof(body), &n) == 0, "");
    check("还原后与 ClientHelloInner 逐字段一致（含扩展顺序）",
          ech_decompress_inner(&srv, body, n, &tpl) == 0 && same_ch(&srv, &inner), "");

    /* 四条 MUST abort：引用缺失扩展 / 重复引用 / 引用 ECH 自己 / 相对顺序被换 */
    bad = squeezed;
    marker[0] = 0x00;
    marker[1] = 0x2B;
    marker[2] = 0x00;
    marker[3] = 0x33;
    marker[4] = 0x12;
    marker[5] = 0x34;
    check("abort 1：引用了 ClientHelloOuter 里没有的扩展 → -4",
          ch_set_ext(&bad, ECH_OUTER_EXTENSIONS, marker, 6) == 0 &&
              ch_encode(&bad, body, sizeof(body), &n) == 0 &&
              ech_decompress_inner(&srv, body, n, &tpl) == -4, "");

    bad = squeezed;
    marker[0] = 0x00;
    marker[1] = 0x33;
    marker[2] = 0x00;
    marker[3] = 0x33;
    check("abort 2：重复引用同一扩展 → -5",
          ch_set_ext(&bad, ECH_OUTER_EXTENSIONS, marker, 4) == 0 &&
              ch_encode(&bad, body, sizeof(body), &n) == 0 &&
              ech_decompress_inner(&srv, body, n, &tpl) == -5, "");

    bad = squeezed;
    marker[0] = 0xFE;
    marker[1] = 0x0D;
    check("abort 3：引用了 encrypted_client_hello 自己 → -6",
          ch_set_ext(&bad, ECH_OUTER_EXTENSIONS, marker, 2) == 0 &&
              ch_encode(&bad, body, sizeof(body), &n) == 0 &&
              ech_decompress_inner(&srv, body, n, &tpl) == -6, "");

    bad = squeezed;
    marker[0] = 0x00;
    marker[1] = 0x33;
    marker[2] = 0x00;
    marker[3] = 0x2B;
    check("abort 4：相对顺序与 outer 相反 → -7",
          ch_set_ext(&bad, ECH_OUTER_EXTENSIONS, marker, 4) == 0 &&
              ch_encode(&bad, body, sizeof(body), &n) == 0 &&
              ech_decompress_inner(&srv, body, n, &tpl) == -7, "");

    /* 压缩省掉的是「重复发送」而不是「不发送」—— 端到端必须仍然逐字节还原 */
    check("（前置）不压缩的端到端封装成功",
          ech_client_encrypt(&bad, &ctx, plain, &plain_len, sizeof(plain), &inner, &tpl,
                             &cfg, NULL, 0) == 0, "");
    check("（前置）压缩的端到端封装成功",
          ech_client_encrypt(&bad, &ctx, shrunk, &shrunk_len, sizeof(shrunk), &inner, &tpl,
                             &cfg, names, 2) == 0, "");
    check("压缩后总长至少省下 32 字节（足以抵消一轮 32 字节对齐）",
          shrunk_len + 32 <= plain_len, "");
    at = ch_ext_index(&bad, ECH_EXT_TYPE);
    check("压缩路径下服务端仍能逐字节还原 ClientHelloInner",
          at >= 0 && ech_server_decrypt(&srv, &bad, sk, pk, &cfg) == 0 &&
              same_ch(&srv, &inner), "");
    return g_checks - before;
}

/* ---------------------------------------------------------------- G 接受确认 */

static int check_confirmation(void)
{
    static uint8_t rnd[32], rnd2[32], transcript[32], out[8], got[8];
    static uint8_t prk[ECH_NH], label[128];
    const char *prefix = "tls13 ech accept confirmation";
    size_t n = 0, label_len;
    int before = g_checks;

    t_fill(rnd, 32, 0xAA);
    t_fill(rnd2, 32, 0xAB);
    check("transcript = SHA-256(ClientHelloInner || ServerHello)",
          t_sha256_2((const uint8_t *)"ClientHelloInner", 16,
                     (const uint8_t *)"ServerHello", 11, transcript) == 0, "");
    check("accept_confirmation 成功且为 8 字节",
          ech_accept_confirmation(rnd, transcript, 32, out) == 0, "");
    check("同一 (inner random, transcript) 必然得到同一值",
          ech_accept_confirmation(rnd, transcript, 32, got) == 0 && memcmp(out, got, 8) == 0,
          "");
    check("换 inner random → 值不同",
          ech_accept_confirmation(rnd2, transcript, 32, got) == 0 &&
              memcmp(out, got, 8) != 0, "");
    check("transcript 多一个字节 → 值不同",
          ech_accept_confirmation(rnd, transcript, 33, got) == 0 &&
              memcmp(out, got, 8) != 0, "");
    check("HRR 版用不同标签，值必然不同",
          ech_hrr_accept_confirmation(rnd, transcript, 32, got) == 0 &&
              memcmp(out, got, 8) != 0, "");

    /* 手工按 RFC 8446 §7.1 拼一遍 HkdfLabel：
     *   u16(L) | u8(len("tls13 "+label)) | "tls13 " + label | u8(len(context)) | context
     * 注意这**不是** HPKE 的 LabeledExpand（那里 I2OSP(L,2) 在最前，且带 "HPKE-v1" 与
     * suite_id 而没有 "tls13 " 前缀）—— 两者长得像、编码完全不同，混用不会报错 */
    label_len = strlen(prefix);
    label[n++] = 0x00;
    label[n++] = 0x08;
    label[n++] = (uint8_t)label_len;
    memcpy(label + n, prefix, label_len);
    n += label_len;
    label[n++] = 0x20;
    memcpy(label + n, transcript, 32);
    n += 32;
    check("手工复算 HkdfLabel 与实现一致",
          ech_extract(NULL, 0, rnd, 32, prk) == 0 &&
              ech_expand(prk, label, n, got, 8) == 0 && memcmp(out, got, 8) == 0, "");
    return g_checks - before;
}

int main(void)
{
    const struct {
        const char *name;
        int (*fn)(void);
    } groups[] = {
        {"RFC 9180 附录 A.2 官方向量", check_hpke_vectors},
        {"ClientHello / ECHConfig 编解码", check_codec},
        {"填充公式与 32 字节对齐", check_padding},
        {"端到端往返", check_roundtrip},
        {"篡改与错密钥必然失败", check_tamper},
        {"ech_outer_extensions 压缩与 4 条 abort", check_outer_ext},
        {"接受确认值", check_confirmation},
    };
    size_t i;

    for (i = 0; i < sizeof(groups) / sizeof(groups[0]); i++) {
        printf("  %s: %d checks\n", groups[i].name, groups[i].fn());
    }
    printf("ech_demo: %d checks, %d failed\n", g_checks, g_failed);
    return g_failed == 0 ? 0 : 1;
}
