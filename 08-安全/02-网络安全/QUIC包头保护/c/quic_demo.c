/*
 * quic_demo.c —— QUIC v1 包保护的可执行自检（C + OpenSSL）
 *
 * 构建（Windows / MSYS2 或 Linux 均可）：
 *   gcc -O2 -Wall -Wextra -o quic_demo quic_demo.c -lcrypto
 *
 * 所有期望值都取自 RFC 原文，逐条对应：
 *   RFC 9001 附录 A.1  Initial 密钥（含五个 HkdfLabel 的 info）
 *   RFC 9001 附录 A.2  客户端 Initial：未保护头、AES-ECB 掩码、受保护头
 *   RFC 9001 附录 A.5  ChaCha20-Poly1305 短头包：从 secret 到 21 字节包
 *   RFC 9000 附录 A.2/A.3  包号编码字节数与解码窗口
 *
 * 本文件是唯一被编译的翻译单元，两个 *_impl.h 由文本级 #include 引入，
 * 因此头里的 static 函数在同一 TU 内可见（无需单独编译）。
 */
#include <stdio.h>
#include <string.h>

#include "quic_kdf_impl.h"
#include "quic_packet_impl.h"
#include "quic_protect_impl.h"

static int g_checks = 0;

static void check(const char *what, int cond)
{
    if (!cond) {
        printf("  FAIL: %s\n", what);
    }
    g_checks += cond ? 1 : 0;
}

static int hex_eq(const uint8_t *got, size_t got_len, const char *expect_hex)
{
    uint8_t expect[64];
    size_t n = strlen(expect_hex) / 2;
    if (got_len != n || n > sizeof(expect)) {
        return 0;
    }
    if (quic_hex2bin(expect_hex, expect, n) != 0) {
        return 0;
    }
    return memcmp(got, expect, n) == 0;
}

/* RFC 9001 附录 A.1：Initial 密钥全链路（含 DCID 派生的 client/server secret） */
static void test_initial_keys(void)
{
    uint8_t dcid[8], csec[32], ssec[32], key[16], iv[12], hp[16], ku[32];

    check("hex decode dcid", quic_hex2bin("8394c8f03e515708", dcid, 8) == 0);
    check("initial secrets", quic_initial_secrets(dcid, 8, csec, ssec) == 0);
    check("client_initial_secret", hex_eq(csec, 32,
        "c00cf151ca5be075ed0ebfb5c80323c42d6b7db67881289af4008f1f6c357aea"));
    check("server_initial_secret", hex_eq(ssec, 32,
        "3c199828fd139efd216c155ad844cc81fb82fa8d7446fa7d78be803acdda951b"));

    check("client initial keys", quic_packet_keys(csec, 16, 12, 16, key, iv, hp) == 0);
    check("client key", hex_eq(key, 16, "1f369613dd76d5467730efcbe3b1a22d"));
    check("client iv", hex_eq(iv, 12, "fa044b2f42a3fd3b46fb255c"));
    check("client hp", hex_eq(hp, 16, "9f50449e04a0e810283a1e9933adedd2"));

    check("server initial keys", quic_packet_keys(ssec, 16, 12, 16, key, iv, hp) == 0);
    check("server key", hex_eq(key, 16, "cf3a5331653c364c88f0f379b6067e37"));
    check("server iv", hex_eq(iv, 12, "0ac1493ca1905853b0bba03e"));
    check("server hp", hex_eq(hp, 16, "c206b8d9b9f0f37644430b490eeaa314"));

    check("quic ku differs from the traffic secret", quic_next_secret(csec, ku) == 0 &&
          !hex_eq(ku, 32, "c00cf151ca5be075ed0ebfb5c80323c42d6b7db67881289af4008f1f6c357aea"));
}

/* RFC 9001 附录 A.2：未保护头解析 + AES-ECB 头保护 */
static void test_initial_header(void)
{
    const char *hdr_hex = "c300000001088394c8f03e5157080000449e00000002";
    uint8_t hdr[64], hp[16], mask[5], buf[64];
    uint64_t length = 0, pn = 0;
    size_t used, pn_offset, n;

    check("hex decode header", quic_hex2bin(hdr_hex, hdr, 22) == 0);
    check("first byte is Initial long header",
          (hdr[0] & 0x80) != 0 && quic_long_header_type(hdr[0]) == 0);
    check("version == 1", hdr[1] == 0 && hdr[2] == 0 && hdr[3] == 0 && hdr[4] == 1);
    check("dcid len 8", hdr[5] == 8);
    check("scid len 0", hdr[14] == 0);

    used = quic_varint_decode(hdr, 22, 15, &length);        /* token length */
    check("token length varint", used == 1 && length == 0);
    used = quic_varint_decode(hdr, 22, 16, &length);        /* length field */
    check("length field is 2 bytes", used == 2);
    check("length == 1182 (4 pn + 1162 frames + 16 tag)",
          length == 1182 && length != 0x449e);

    pn_offset = quic_pn_offset_initial(8, 0, 0, used);
    check("pn_offset == 18", pn_offset == 18);
    check("pn length 4", quic_pn_length_from_first_byte(hdr[0]) == 4);
    for (n = 0; n < 4; n++) {
        pn = (pn << 8) | hdr[pn_offset + n];
    }
    check("packet number == 2", pn == 2);

    /* 附录 A.2 给出 sample（= 受保护负载前 16 字节）与 AES-ECB 掩码 */
    check("aes hp mask", quic_hex2bin("9f50449e04a0e810283a1e9933adedd2", hp, 16) == 0 &&
          quic_hp_mask_aes(hp, (const uint8_t *)"\xd1\xb1\xc9\x8d\xd7\x68\x9f\xb8"
                          "\xec\x11\xd2\x42\xb1\x23\xdc\x9b", mask) == 0 &&
          hex_eq(mask, 5, "437b9aec36"));

    memcpy(buf, hdr, 22);
    quic_apply_header_protection(buf, pn_offset, 4, mask);
    check("protected header", hex_eq(buf, 22,
        "c000000001088394c8f03e5157080000449e7b9aec34"));
    /* 长头只掩低 4 位：类型位（bit5..4）与固定位必须原样保留 */
    check("long header keeps type bits",
          (buf[0] & 0x30) == (hdr[0] & 0x30) && (buf[0] & 0x80) != 0);
}

/* RFC 9001 附录 A.5：ChaCha20-Poly1305 短头包的完整端到端向量 */
static void test_chacha20_short_packet(void)
{
    uint8_t secret[32], key[32], iv[12], hp[32], ku[32];
    uint8_t nonce[12], mask[5], buf[64], pt[64];
    uint8_t pn_field[4];
    size_t sealed_len = 0, pt_len = 0, pn_offset;
    uint64_t pn;

    check("hex decode a5 secret", quic_hex2bin(
        "9ac312a7f877468ebe69422748ad00a15443f18203a07d6060f688f30f21632b",
        secret, 32) == 0);
    check("a5 packet keys", quic_packet_keys(secret, 32, 12, 32, key, iv, hp) == 0);
    check("a5 key", hex_eq(key, 32,
        "c6d98ff3441c3fe1b2182094f69caa2ed4b716b65488960a7a984979fb23e1c8"));
    check("a5 iv", hex_eq(iv, 12, "e0459b3474bdd0e44a41c144"));
    check("a5 hp", hex_eq(hp, 32,
        "25a282b9e82f06f21f488917a4fc8f1b73573685608597d0efcb076b0ab7a7a4"));
    check("a5 ku", quic_next_secret(secret, ku) == 0 && hex_eq(ku, 32,
        "1223504755036d556342ee9361d253421a826c9ecdf3c7148684b36b714881f9"));

    /* pn = 654360564：示例算法给 4 字节，附录 A.5 为了省掉 PADDING 帧改用 3 字节 */
    check("sample alg gives 4 bytes for this pn",
          quic_pn_encode_bytes(654360564ULL, 0, 0) == 4);
    quic_pn_encode(654360564ULL, 3, pn_field);
    check("pn low 3 bytes", hex_eq(pn_field, 3, "00bff4"));
    quic_aead_nonce(iv, 12, 654360564ULL, nonce);
    check("a5 nonce", hex_eq(nonce, 12, "e0459b3474bdd0e46d417eb0"));

    /* 未保护头 = 4200bff4（短头 + 密钥相位 0 + 3 字节包号），AAD 就是它 */
    buf[0] = 0x42;
    memcpy(buf + 1, pn_field, 3);
    check("a5 seal", quic_aead_seal(key, nonce, buf, 4, (const uint8_t *)"\x01", 1,
                                    buf + 4, &sealed_len) == 0);
    check("a5 payload ciphertext", sealed_len == 17 && hex_eq(buf + 4, 17,
        "655e5cd55c41f69080575d7999c25a5bfb"));

    /* 采样从 pn_offset(=1) + 4 起取 16 字节：3 字节包号跳过 1 字节负载 */
    pn_offset = quic_pn_offset_short(0);
    check("short pn_offset == 1", pn_offset == 1);
    check("sample available", quic_sample_ok(21, pn_offset));
    check("no sample for 20-byte packet", !quic_sample_ok(20, pn_offset));
    check("chacha20 hp mask",
          quic_hp_mask_chacha20(hp, buf + pn_offset + 4, mask) == 0 &&
          hex_eq(mask, 5, "aefefe7d03"));

    quic_apply_header_protection(buf, pn_offset, 3, mask);
    check("protected 21-byte packet", hex_eq(buf, 21,
        "4cfe4189655e5cd55c41f69080575d7999c25a5bfb"));

    /* 接收端：先去头保护再解 AEAD，且只能用「上一个已处理包号」还原完整包号 */
    check("remove hp -> pn_len 3",
          quic_remove_header_protection(buf, 21, pn_offset, 1, hp) == 3);
    check("unprotected header restored", hex_eq(buf, 4, "4200bff4"));
    pn = quic_pn_decode(654360563ULL, 0x00bff4ULL, 24);
    check("decoded pn == 654360564", pn == 654360564ULL);
    check("a5 open", quic_aead_open(key, nonce, buf, 4, buf + 4, 17,
                                    pt, &pt_len) == 0 && pt_len == 1 && pt[0] == 0x01);

    /* 篡改一字节后必须认证失败，且不得写出明文 */
    buf[20] ^= 0x01;
    check("tampered packet rejected",
          quic_aead_open(key, nonce, buf, 4, buf + 4, 17, pt, &pt_len) != 0);
    buf[20] ^= 0x01;
}

/* RFC 9000 §16：varint 边界与「不要求最短编码」 */
static void test_varint(void)
{
    uint64_t vals[8];
    size_t i, n;
    uint8_t enc[8];
    uint64_t got = 0;

    vals[0] = 0; vals[1] = 63; vals[2] = 64; vals[3] = 16383;
    vals[4] = 16384; vals[5] = (1ULL << 30) - 1; vals[6] = 1ULL << 30;
    vals[7] = QUIC_VARINT_MAX;
    for (i = 0; i < 8; i++) {
        n = quic_varint_encode(vals[i], enc);
        check("varint roundtrip", quic_varint_decode(enc, n, 0, &got) == n &&
                                  got == vals[i]);
    }
    check("varint widths",
          quic_varint_len(63) == 1 && quic_varint_len(64) == 2 &&
          quic_varint_len(16384) == 4 && quic_varint_len(1ULL << 30) == 8);
    check("non-minimal encoding is legal",
          quic_varint_decode((const uint8_t *)"\x40\x3f", 2, 0, &got) == 2 &&
          got == 63);
    check("truncated varint rejected",
          quic_varint_decode((const uint8_t *)"\x40", 1, 0, &got) == 0);
}

/* RFC 9000 附录 A.2/A.3：包号字节数与窗口还原 */
static void test_packet_number(void)
{
    uint8_t enc[4];
    uint64_t largest = 0xABE8B3ULL;

    check("A.2 0xac5c02 -> 2 bytes", quic_pn_encode_bytes(0xAC5C02ULL, 1, largest) == 2);
    quic_pn_encode(0xAC5C02ULL, 2, enc);
    check("A.2 truncated value", hex_eq(enc, 2, "5c02"));
    check("A.2 0xace8fe -> 3 bytes", quic_pn_encode_bytes(0xACE8FEULL, 1, largest) == 3);
    check("A.3 decode 16-bit", quic_pn_decode(largest, 0x5C02ULL, 16) == 0xAC5C02ULL);
    check("A.3 decode 24-bit", quic_pn_decode(largest, 0xACE8FEULL, 24) == 0xACE8FEULL);

    /* 从未收到 ACK 时按 full_pn + 1 计：包号 2 只需 1 字节 */
    check("first packet uses 1 byte", quic_pn_encode_bytes(2, 0, 0) == 1);
    /* 2 的幂是分界点：n=128 恰好 8 位 → 1 字节，n=129 → 2 字节 */
    check("power-of-two boundary",
          quic_pn_encode_bytes(127, 1, 0) == 1 && quic_pn_encode_bytes(128, 1, 0) == 1 &&
          quic_pn_encode_bytes(129, 1, 0) == 2);
    /* 无符号下溢哨兵：largest_pn 很小时第一个分支不得被误触发 */
    check("no unsigned underflow", quic_pn_decode(0, 0x40ULL, 8) == 0x40ULL);
    check("window edges",
          quic_pn_decode(0x0000FFULL, 0x00ULL, 8) == 0x000100ULL &&
          quic_pn_decode(0x000100ULL, 0xFFULL, 8) == 0x0000FFULL);
    check("min frame length", quic_min_frame_length(1, 16) == 3 &&
          quic_min_frame_length(2, 16) == 2 && quic_min_frame_length(3, 16) == 1 &&
          quic_min_frame_length(4, 16) == 0);
}

int main(void)
{
    int before;

    before = g_checks;
    test_initial_keys();
    printf("  RFC 9001 A.1 initial keys:        %d checks\n", g_checks - before);

    before = g_checks;
    test_initial_header();
    printf("  RFC 9001 A.2 initial header:      %d checks\n", g_checks - before);

    before = g_checks;
    test_chacha20_short_packet();
    printf("  RFC 9001 A.5 chacha20 short pkt:  %d checks\n", g_checks - before);

    before = g_checks;
    test_varint();
    printf("  RFC 9000 §16 varint:              %d checks\n", g_checks - before);

    before = g_checks;
    test_packet_number();
    printf("  RFC 9000 A.2/A.3 packet number:   %d checks\n", g_checks - before);

    printf("quic_demo: %d checks passed\n", g_checks);
    return 0;
}
