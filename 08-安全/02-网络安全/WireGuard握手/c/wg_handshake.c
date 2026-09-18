// WireGuard 握手演示主程序（校验脚本）：编译
//   gcc -O2 -Wall -Wextra -pedantic wg_handshake.c -lcrypto -o wg_demo
// 实现分散在 wg_crypto_impl.h（BLAKE2s/HMAC/KDF）与 wg_noise_impl.h（X25519、AEAD、哈希链），
// 两者以文本级 #include 进本文件，因此构建命令无需列出它们。

#include "wg_noise_impl.h"

int main(void) {
    uint8_t a_priv[PUB_LEN], a_pub[PUB_LEN], b_priv[PUB_LEN], b_pub[PUB_LEN];
    uint8_t eph_priv[PUB_LEN], eph_pub[PUB_LEN], es[PUB_LEN], ss[PUB_LEN], ee[PUB_LEN];
    uint8_t psk[SYM_LEN], mac1_key[SYM_LEN];
    Symmetric sy_a, sy_b;
    uint8_t label[8 + PUB_LEN];

    if (!x25519_keypair(a_priv, a_pub) || !x25519_keypair(b_priv, b_pub) ||
        !x25519_keypair(eph_priv, eph_pub)) {
        fprintf(stderr, "X25519 keygen failed\n");
        return 1;
    }
    memset(psk, 0x33, sizeof(psk));

    /* --- 协议常量与消息长度 --- */
    check("LEN_INITIATION == 148", LEN_INITIATION == 148, LEN_INITIATION, 148);
    check("LEN_RESPONSE == 92", LEN_RESPONSE == 92, LEN_RESPONSE, 92);
    check("LEN_DATA_HDR == 16", LEN_DATA_HDR == 16, LEN_DATA_HDR, 16);
    check("handshake_name 长度 37",
          sizeof(HANDSHAKE_NAME) - 1 == 37, (long)(sizeof(HANDSHAKE_NAME) - 1), 37);
    check("identifier_name 长度 34",
          sizeof(IDENTIFIER_NAME) - 1 == 34, (long)(sizeof(IDENTIFIER_NAME) - 1), 34);

    /* --- BLAKE2s 官方向量：BLAKE2s-256("abc") --- */
    {
        static const uint8_t want[HASH_LEN] = {
            0x50, 0x8C, 0x5E, 0x8C, 0x32, 0x7C, 0x14, 0xE2, 0xE1, 0xA7, 0x2B, 0xA3,
            0x4E, 0xEB, 0x45, 0x2F, 0x37, 0x45, 0x8B, 0x20, 0x9E, 0xD6, 0x3A, 0x29,
            0x4D, 0x99, 0x9B, 0x4C, 0x86, 0x67, 0x59, 0x82
        };
        uint8_t got[HASH_LEN];
        int i, diff = 0;
        blake2s(got, HASH_LEN, NULL, 0, (const uint8_t *)"abc", 3);
        for (i = 0; i < HASH_LEN; i++) diff |= got[i] ^ want[i];
        check("BLAKE2s-256(\"abc\") 官方向量", diff == 0, diff, 0);
    }

    /* --- 两侧各自推进 msg1：ck/h 必须逐步一致 --- */
    handshake_init(&sy_a, b_pub);   /* 发起方视角：pre-message 是响应方静态公钥 */
    handshake_init(&sy_b, b_pub);   /* 响应方视角：pre-message 是自己的静态公钥 */
    check("ck0 相等", memcmp(sy_a.ck, sy_b.ck, HASH_LEN) == 0, 0, 0);
    check("h0 相等", memcmp(sy_a.h, sy_b.h, HASH_LEN) == 0, 0, 0);

    /* e：发起方临时公钥（PSK 握手还要用同一公钥做一次 MixKey(ck)） */
    mix_hash(&sy_a, eph_pub, PUB_LEN);
    mix_key_only_ck(&sy_a, eph_pub, PUB_LEN);
    mix_hash(&sy_b, eph_pub, PUB_LEN);
    mix_key_only_ck(&sy_b, eph_pub, PUB_LEN);
    check("e token 后 h 相等", memcmp(sy_a.h, sy_b.h, HASH_LEN) == 0, 0, 0);

    /* es / ss：两侧用不同私钥算同一个 DH */
    if (!x25519(es, eph_priv, b_pub) || !x25519(ss, a_priv, b_pub)) {
        fprintf(stderr, "X25519 derive failed\n");
        return 1;
    }
    {
        uint8_t es_b[PUB_LEN], ss_b[PUB_LEN];
        x25519(es_b, b_priv, eph_pub);
        x25519(ss_b, b_priv, a_pub);
        check("DH(es) 两侧一致", memcmp(es, es_b, PUB_LEN) == 0, 0, 0);
        check("DH(ss) 两侧一致", memcmp(ss, ss_b, PUB_LEN) == 0, 0, 0);
    }
    mix_key(&sy_a, es, PUB_LEN);
    mix_key(&sy_b, es, PUB_LEN);
    /* s 字段被加密（48 字节 = 32 + 16 tag），这里只验证长度与混入 h */
    {
        uint8_t ct[PUB_LEN + TAG_LEN];
        aead_seal(ct, sy_a.k, sy_a.h, HASH_LEN, a_pub, PUB_LEN);
        check("encrypted_static 长度 48", sizeof(ct) == 48, (long)sizeof(ct), 48);
        mix_hash(&sy_a, ct, sizeof(ct));
        mix_hash(&sy_b, ct, sizeof(ct));
    }
    mix_key(&sy_a, ss, PUB_LEN);
    mix_key(&sy_b, ss, PUB_LEN);
    check("msg1 结束时 h 相等", memcmp(sy_a.h, sy_b.h, HASH_LEN) == 0, 0, 0);
    check("msg1 结束时 ck 相等", memcmp(sy_a.ck, sy_b.ck, HASH_LEN) == 0, 0, 0);

    /* --- 用 h 作为 AD 造一条 12 字节 TAI64N 时间戳（时间只影响明文，不影响链） */
    {
        uint8_t ts[TS_LEN], sealed_ts[TS_LEN + TAG_LEN];
        uint64_t now = 0x400000000000000AULL + 1700000000ULL;
        int i;
        for (i = 0; i < 8; i++) ts[i] = (uint8_t)(now >> (8 * (7 - i)));
        memset(ts + 8, 0, 4);
        aead_seal(sealed_ts, sy_a.k, sy_a.h, HASH_LEN, ts, TS_LEN);
        check("encrypted_timestamp 长度 28", sizeof(sealed_ts) == 28,
              (long)sizeof(sealed_ts), 28);
        mix_hash(&sy_a, sealed_ts, sizeof(sealed_ts));
        mix_hash(&sy_b, sealed_ts, sizeof(sealed_ts));
    }

    /* --- msg2：e → ee → se → psk → {} --- */
    {
        uint8_t eph2_priv[PUB_LEN], eph2_pub[PUB_LEN], ct[TAG_LEN];
        uint8_t newk_a[SYM_LEN], newk_b[SYM_LEN], ck_split_a[HASH_LEN], ck_split_b[HASH_LEN];
        x25519_keypair(eph2_priv, eph2_pub);
        mix_hash(&sy_b, eph2_pub, PUB_LEN);
        mix_key_only_ck(&sy_b, eph2_pub, PUB_LEN);
        mix_hash(&sy_a, eph2_pub, PUB_LEN);
        mix_key_only_ck(&sy_a, eph2_pub, PUB_LEN);
        if (!x25519(ee, eph2_priv, eph_pub)) return 1;
        mix_key(&sy_b, ee, PUB_LEN);
        mix_key(&sy_a, ee, PUB_LEN);
        {   /* se：响应方用自方临时私钥 + 发起方静态公钥 */
            uint8_t se_b[PUB_LEN], se_a[PUB_LEN];
            x25519(se_b, eph2_priv, a_pub);
            x25519(se_a, a_priv, eph2_pub);
            check("DH(se) 两侧一致", memcmp(se_b, se_a, PUB_LEN) == 0, 0, 0);
            mix_key(&sy_b, se_b, PUB_LEN);
            mix_key(&sy_a, se_a, PUB_LEN);
        }
        mix_key_and_hash(&sy_b, psk);
        mix_key_and_hash(&sy_a, psk);
        check("psk 混入后 h 相等", memcmp(sy_a.h, sy_b.h, HASH_LEN) == 0, 0, 0);
        aead_seal(ct, sy_b.k, sy_b.h, HASH_LEN, NULL, 0);
        check("encrypted_nothing 长度 16", sizeof(ct) == TAG_LEN, (long)sizeof(ct), 16);
        mix_hash(&sy_b, ct, TAG_LEN);
        mix_hash(&sy_a, ct, TAG_LEN);
        /* split(): HKDF(ck, 空输入, 2) → 双向传输密钥 */
        kdf(newk_a, newk_b, NULL, NULL, 0, sy_a.ck);
        memcpy(ck_split_a, sy_a.ck, HASH_LEN);
        memcpy(ck_split_b, sy_b.ck, HASH_LEN);
        check("Split 前 ck 相等", memcmp(ck_split_a, ck_split_b, HASH_LEN) == 0, 0, 0);
        check("两个方向密钥不同", memcmp(newk_a, newk_b, SYM_LEN) != 0, 0, 0);
        /* 传输消息：KEY_IDX + 计数器 + 密文 */
        {
            uint8_t dm[LEN_DATA_HDR + 32], pt[16];
            size_t i;
            memset(pt, 0xAB, sizeof(pt));
            dm[0] = 4; dm[1] = dm[2] = dm[3] = 0;
            for (i = 0; i < 8; i++) dm[8 + i] = (uint8_t)(i == 0 ? 7 : 0);
            aead_seal(dm + LEN_DATA_HDR, newk_a, NULL, 0, pt, sizeof(pt));
            check("数据消息总长 = 16+16+16", sizeof(dm) == 48, (long)sizeof(dm), 48);
        }
    }

    /* --- MAC1：密钥 = BLAKE2s("mac1----" ‖ 响应方静态公钥)，输出 16 字节 --- */
    memcpy(label, MAC1_LABEL, 8);
    memcpy(label + 8, b_pub, PUB_LEN);
    blake2s(mac1_key, SYM_LEN, NULL, 0, label, sizeof(label));
    {
        uint8_t init_msg[LEN_INITIATION];
        uint8_t other_key[SYM_LEN], label2[8 + PUB_LEN];
        uint8_t other_priv[PUB_LEN], other_pub[PUB_LEN];
        uint8_t mac_right[COOKIE_LEN], mac_wrong[COOKIE_LEN], mac_tampered[COOKIE_LEN];

        memset(init_msg, 0, sizeof(init_msg));
        init_msg[0] = 1;                        /* type = HANDSHAKE_INITIATION */
        memcpy(init_msg + 8, eph_pub, PUB_LEN); /* 这里只求确定字节串，不做真加密 */
        blake2s(mac_right, COOKIE_LEN, mac1_key, SYM_LEN,
                init_msg, LEN_INITIATION - MACS_LEN);

        x25519_keypair(other_priv, other_pub);  /* 换一个「响应方」静态公钥 */
        memcpy(label2, MAC1_LABEL, 8);
        memcpy(label2 + 8, other_pub, PUB_LEN);
        blake2s(other_key, SYM_LEN, NULL, 0, label2, sizeof(label2));
        blake2s(mac_wrong, COOKIE_LEN, other_key, SYM_LEN,
                init_msg, LEN_INITIATION - MACS_LEN);

        init_msg[8] ^= 0x01;                    /* 改一个字节 */
        blake2s(mac_tampered, COOKIE_LEN, mac1_key, SYM_LEN,
                init_msg, LEN_INITIATION - MACS_LEN);

        check("MAC1 长度 16", COOKIE_LEN == 16, COOKIE_LEN, 16);
        check("换响应方公钥 → MAC1 不同",
              memcmp(mac_right, mac_wrong, COOKIE_LEN) != 0, 0, 0);
        check("改消息一个字节 → MAC1 不同",
              memcmp(mac_right, mac_tampered, COOKIE_LEN) != 0, 0, 0);
    }
    /* cookie：keyed-BLAKE2s(secret, 源IP ‖ 源端口)，换端口即不同 */
    {
        uint8_t secret[SYM_LEN], c1[COOKIE_LEN], c2[COOKIE_LEN], buf[6];
        memset(secret, 0x77, sizeof(secret));
        memset(buf, 0, sizeof(buf));
        buf[3] = 1;
        buf[4] = 0xCA; buf[5] = 0x6C;               /* 端口 51820 */
        blake2s(c1, COOKIE_LEN, secret, SYM_LEN, buf, sizeof(buf));
        buf[5] = 0x6D;                              /* 端口 51821 */
        blake2s(c2, COOKIE_LEN, secret, SYM_LEN, buf, sizeof(buf));
        check("cookie 绑定源端口", memcmp(c1, c2, COOKIE_LEN) != 0, 0, 0);
    }
    /* cookie 加密密钥：BLAKE2s("cookie--" ‖ 响应方静态公钥)，与 MAC1 密钥同构但不同 */
    {
        uint8_t ck_key[SYM_LEN], label3[8 + PUB_LEN];
        memcpy(label3, COOKIE_LABEL, 8);
        memcpy(label3 + 8, b_pub, PUB_LEN);
        blake2s(ck_key, SYM_LEN, NULL, 0, label3, sizeof(label3));
        check("cookie 密钥 ≠ MAC1 密钥", memcmp(ck_key, mac1_key, SYM_LEN) != 0, 0, 0);
    }

    printf("\n%s: %d failure(s)\n", failures ? "FAILED" : "all checks passed", failures);
    return failures ? 1 : 0;
}
