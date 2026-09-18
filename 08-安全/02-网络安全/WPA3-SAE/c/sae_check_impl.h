/*
 * sae_check_impl.h —— 共用的自检脚手架 + 群参数 / 密码元素的断言
 *
 * 断言分三类：A 可由规范直接推出的等式；B 协议必须成立的性质；
 * C 与 Python / Go 共享的固定标量向量（比 sha256 摘要，避免在源码里塞 2048 位常量）。
 * 构建时只需编译 sae_demo.c —— 三个实现头都是文本级包含。
 */
#ifndef SAE_CHECK_IMPL_H
#define SAE_CHECK_IMPL_H

#include <stdio.h>

#include "sae_peer_impl.h"

/* OpenSSL 3.0 起 BN_is_prime_ex 被 BN_check_prime 取代 */
#if OPENSSL_VERSION_NUMBER >= 0x30000000L
#define SAE_IS_PRIME(n, ctx) (BN_check_prime((n), (ctx), NULL) == 1)
#else
#define SAE_IS_PRIME(n, ctx) (BN_is_prime_ex((n), 64, (ctx), NULL) == 1)
#endif

/* 与 Python / Go 共用的固定标量（真实实现必须用随机数，这里为了可比性写死） */
#define VEC_PW "password"
#define VEC_PRIV_A "0102030405060708090A0B0C0D0E0F10"
#define VEC_MASK_A "1112131415161718191A1B1C1D1E1F20"
#define VEC_PRIV_B "2122232425262728292A2B2C2D2E2F30"
#define VEC_MASK_B "3132333435363738393A3B3C3D3E3F40"
#define VEC_SCALAR_A "121416181A1C1E20222426282A2C2E30"
#define VEC_SCALAR_B "525456585A5C5E60626466686A6C6E70"
#define VEC_PE_SHA "3fc647ccc7eb193dd5cb02744ab43ad28d059803187b9c31425f560ef3c49e2e"
#define VEC_ELEM_A_SHA "3a880829d0f928f5a97280169555bc06fce8ef0f58384c19a9ed2a9a8775191a"
#define VEC_SS_SHA "2b0064354b3485159da05a2d8c09fd75efe23e0b6e065c1662cd22eca6168eae"
#define VEC_KCK_SHA "eaa108559fa7ef14e65d4c0076d558374d5bf59cdeb596ac8ba542ae5ad6a0e2"
#define VEC_CONFIRM_A "60c111a2a5f1821ce0541eb45cddf321950354a0f3912a25d07977af7c2ad402"
#define VEC_CONFIRM_B "39a4888b1951f08081acc00c53952170e1fa7f1979d1a0bd44839eccaf95d28e"
#define VEC_WRONG_PE_SHA "188b156513907cfb4a5b140b96be1b6e3c27e751fb602a8440a7907bb2f3a750"

static int g_checks = 0;
static int g_failures = 0;

static void check(const char *label, int cond, const char *detail)
{
    g_checks++;
    if (!cond) {
        g_failures++;
        printf("  FAIL %s%s%s\n", label, detail ? " : " : "", detail ? detail : "");
    }
}

static void bytes_to_hex(const uint8_t *p, size_t n, char *out)
{
    static const char *HX = "0123456789abcdef";
    size_t i;
    for (i = 0; i < n; i++) {
        out[i * 2] = HX[p[i] >> 4];
        out[i * 2 + 1] = HX[p[i] & 0x0f];
    }
    out[n * 2] = '\0';
}

/* k 小一些的 peer，供只关心「校验是否被触发」的用例使用（狩猎与啄食太贵） */
static void fresh_peer(sae_peer *pr, const sae_group *gr, BN_CTX *ctx)
{
    if (sae_peer_init(pr, gr, VEC_PW, "alice", "bob", 1) != 0 ||
        sae_peer_commit(pr, gr, VEC_PRIV_A, VEC_MASK_A, ctx) != 0) {
        check("构造 fresh_peer", 0, NULL);
    }
}

static void check_group(const sae_group *gr, BN_CTX *ctx)
{
    BIGNUM *t = BN_new(), *pm1 = BN_new(), *inv = BN_new();
    int nonres = 0, x;

    check("p 是 2048 位", BN_num_bits(gr->p) == 2048, NULL);
    check("p 与 RFC 3526 §3 原文逐字符一致", sae_bn_eq_hex(gr->p, SAE_P_HEX), NULL);
    check("生成元 G = 2", BN_is_word(gr->g, 2) != 0, NULL);
    check("p ≡ 3 (mod 4)", BN_mod_word(gr->p, 4) == 3, NULL);
    BN_copy(pm1, gr->p);
    BN_sub_word(pm1, 1);
    BN_rshift1(pm1, pm1);
    check("q = (p-1)/2", BN_cmp(gr->q, pm1) == 0, NULL);
    check("(p-1)/q = 2（安全素数的标志）", BN_is_word(gr->exp_peck, 2) != 0, NULL);
    check("p 是素数", SAE_IS_PRIME(gr->p, ctx), NULL);
    check("q 也是素数（故 p 是安全素数）", SAE_IS_PRIME(gr->q, ctx), NULL);

    check("2^q = 1，即 G 的阶恰为 q", sae_scalar_op(gr, gr->q, gr->g, t, ctx) == 0 &&
          BN_is_one(t), NULL);
    check("G 是合法子群元素", sae_is_valid_element(gr, gr->g, ctx), NULL);
    BN_copy(pm1, gr->p);
    BN_sub_word(pm1, 1);
    check("p-1 不是合法元素（(-1)^q = -1）",
          !sae_is_valid_element(gr, pm1, ctx), NULL);
    BN_set_word(t, 1);
    check("1 不是合法元素", !sae_is_valid_element(gr, t, ctx), NULL);
    BN_set_word(t, 0);
    check("0 不是合法元素", !sae_is_valid_element(gr, t, ctx), NULL);
    BN_copy(t, gr->p);
    check("p 不是合法元素（必须严格小于 p-1）", !sae_is_valid_element(gr, t, ctx), NULL);

    for (x = 3; x < 100; x++) {                 /* 找一个小非二次剩余作对照 */
        BN_set_word(t, (BN_ULONG)x);
        if (!sae_is_valid_element(gr, t, ctx)) {
            nonres = x;
            break;
        }
    }
    check("存在不属于子群的样本", nonres != 0, NULL);
    if (nonres != 0) {
        BN_set_word(t, (BN_ULONG)nonres);
        BN_mod_sqr(t, t, gr->p, ctx);           /* 非二次剩余的平方必在子群内 */
        check("非二次剩余的平方回到子群", sae_is_valid_element(gr, t, ctx), NULL);
    }

    BN_set_word(t, 12345);
    BN_mod_inverse(inv, t, gr->p, ctx);
    BN_mod_mul(t, t, inv, gr->p, ctx);
    check("r · r^-1 mod p = 1", BN_is_one(t) != 0, NULL);

    BN_free(t);
    BN_free(pm1);
    BN_free(inv);
}

static void check_pwe(const sae_group *gr, BN_CTX *ctx)
{
    sae_peer a, swapped, other;
    uint8_t base[SAE_HASH_LEN], kdf_out[SAE_P_BYTES + 8], buf[64];
    char hx[65];
    BIGNUM *seed = BN_new(), *pm1 = BN_new(), *t = BN_new();
    size_t n = 0;

    if (sae_peer_init(&a, gr, VEC_PW, "alice", "bob", 40) != 0) {
        check("推导 PE", 0, NULL);
        return;
    }
    check("PE 是合法子群元素", sae_is_valid_element(gr, a.pe, ctx), NULL);
    check("PE > 1", sae_bn_ge_word(a.pe, 2), NULL);
    check("狩猎与啄食至少跑满 k=40 轮", a.iterations == 40, NULL);
    check("PE 摘要与 Python/Go 一致",
          sae_bn_sha256_hex(a.pe, SAE_P_BYTES, hx) == 0 && strcmp(hx, VEC_PE_SHA) == 0,
          hx);

    /* 身份用 max/min 排序：双方各自视角必须算出同一个 PE */
    sae_peer_init(&swapped, gr, VEC_PW, "bob", "alice", 1);
    check("PE 与身份顺序无关", BN_cmp(a.pe, swapped.pe) == 0, NULL);
    sae_peer_init(&other, gr, "wrong-password", "alice", "bob", 40);
    check("不同密码 → 不同 PE", BN_cmp(a.pe, other.pe) != 0, NULL);
    check("错密码 PE 摘要与 Python/Go 一致",
          sae_bn_sha256_hex(other.pe, SAE_P_BYTES, hx) == 0 &&
          strcmp(hx, VEC_WRONG_PE_SHA) == 0, hx);

    /* A 类：PE 必须等于「counter=1 的 base → KDF-(len(p)+64) → mod (p-1) + 1 → 再啄食」 */
    memcpy(buf + n, "bob", 3);                  /* max(alice,bob) = "bob" */
    n += 3;
    memcpy(buf + n, "alice", 5);
    n += 5;
    memcpy(buf + n, VEC_PW, 8);
    n += 8;
    buf[n++] = 1;
    check("构造第一轮 base", sae_sha256(buf, n, base) == 0, NULL);
    check("KDF-(len(p)+64)", sae_kdf(SAE_PWE_LABEL, base, sizeof(base), kdf_out,
                                     sizeof(kdf_out)) == 0, NULL);
    BN_bin2bn(kdf_out, (int)sizeof(kdf_out), seed);
    BN_copy(pm1, gr->p);
    BN_sub_word(pm1, 1);
    BN_mod(seed, seed, pm1, ctx);
    BN_add_word(seed, 1);
    BN_mod_exp(t, seed, gr->exp_peck, gr->p, ctx);
    check("PE == seed^((p-1)/q) mod p", BN_cmp(t, a.pe) == 0, NULL);
    BN_mod_sqr(t, seed, gr->p, ctx);
    check("安全素数群下啄食就是平方（第一轮即命中）", BN_cmp(t, a.pe) == 0, NULL);

    sae_peer_free(&a);
    sae_peer_free(&swapped);
    sae_peer_free(&other);
    BN_free(seed);
    BN_free(pm1);
    BN_free(t);
}
#endif /* SAE_CHECK_IMPL_H */
