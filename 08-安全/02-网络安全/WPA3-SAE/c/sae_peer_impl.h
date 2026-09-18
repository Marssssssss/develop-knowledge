/*
 * sae_peer_impl.h —— SAE 一端的完整状态机（提交 → 吸收 → 确认）
 *
 * 对应 Python 的 SaePeer。把「状态机」与「群运算/交换公式」分开，是因为前者是
 * 协议状态与校验顺序（谁能调用谁、失败返回什么），后者是纯数学 —— 两类 bug 的排查路径不同。
 *
 * 本文件里的自检辅助（定宽序列化、常数时间比较、定长摘要）在 sae_demo.c 中被大量复用。
 */
#ifndef SAE_PEER_IMPL_H
#define SAE_PEER_IMPL_H

#include "sae_exchange_impl.h"

typedef struct {
    const char *name;                   /* 自己的标识（参与确认值） */
    const char *peer_name;              /* 对端标识 */
    BIGNUM *pe;                         /* 共享的密码元素 */
    BIGNUM *priv;                       /* private，全程保密 */
    BIGNUM *mask;                       /* commit 后立即销毁 */
    BIGNUM *scalar;                     /* 要发给对端的标量 */
    BIGNUM *element;                    /* 要发给对端的元素 */
    BIGNUM *peer_scalar;
    BIGNUM *peer_element;
    BIGNUM *ss;                         /* 共享密钥（群元素） */
    BIGNUM *kck;                        /* 用于确认交换 */
    BIGNUM *mk;                         /* 用于导出会话密钥（本 demo 只验证相等性） */
    int iterations;                     /* 狩猎与啄食实际跑了几轮 */
    int committed;
    int have_keys;
} sae_peer;

/* ------------------------------------------------ 自检辅助 */

/* 与 16 进制串逐字符比较（BN_bn2hex 输出大写、无前导零） */
static int sae_bn_eq_hex(const BIGNUM *v, const char *hex)
{
    char *s = BN_bn2hex(v);
    int ok;
    if (s == NULL) {
        return 0;
    }
    ok = strcmp(s, hex) == 0;
    OPENSSL_free(s);
    return ok;
}

/* 定宽大端序列化后的 sha256，输出 64 个小写 16 进制字符 —— 跨语言比 2048 位常量太笨重 */
static int sae_bn_sha256_hex(const BIGNUM *v, size_t width, char out[65])
{
    static const char *HX = "0123456789abcdef";
    uint8_t buf[SAE_P_BYTES], dig[SAE_HASH_LEN];
    size_t i;
    if (width > sizeof(buf)) {
        return -1;
    }
    if (BN_bn2binpad(v, buf, (int)width) != (int)width) {
        return -1;
    }
    if (sae_sha256(buf, width, dig) != 0) {
        return -1;
    }
    for (i = 0; i < SAE_HASH_LEN; i++) {
        out[i * 2] = HX[dig[i] >> 4];
        out[i * 2 + 1] = HX[dig[i] & 0x0f];
    }
    out[64] = '\0';
    return 0;
}

/* 常数时间比较：确认值比对**不能**按字节提前返回，否则泄漏前缀匹配长度 */
static int sae_ct_eq(const uint8_t *a, const uint8_t *b, size_t n)
{
    uint8_t diff = 0;
    size_t i;
    for (i = 0; i < n; i++) {
        diff |= (uint8_t)(a[i] ^ b[i]);
    }
    return diff == 0;
}

static int sae_bn_in_range(const BIGNUM *x, const BIGNUM *q)
{
    BIGNUM *one = BN_new(), *qm1 = BN_new();
    int ok;
    if (one == NULL || qm1 == NULL) {
        BN_free(one);
        BN_free(qm1);
        return 0;
    }
    ok = BN_set_word(one, 1) == 1 && BN_copy(qm1, q) != NULL &&
         BN_sub_word(qm1, 1) == 1 && BN_cmp(x, one) > 0 && BN_cmp(x, qm1) < 0;
    BN_free(one);
    BN_free(qm1);
    return ok;
}

static int sae_bn_ge_word(const BIGNUM *v, unsigned long w)
{
    BIGNUM *t = BN_new();
    int ok;
    if (t == NULL) {
        return 0;
    }
    ok = BN_set_word(t, w) == 1 && BN_cmp(v, t) >= 0;
    BN_free(t);
    return ok;
}

/* ------------------------------------------------ 状态机 */

static int sae_peer_init(sae_peer *pr, const sae_group *gr, const char *pw,
                         const char *name, const char *peer_name, int k)
{
    memset(pr, 0, sizeof(*pr));
    pr->name = name;
    pr->peer_name = peer_name;
    pr->pe = BN_new();
    if (pr->pe == NULL) {
        return -1;
    }
    return sae_pwe(gr, (const uint8_t *)pw, strlen(pw),
                   (const uint8_t *)name, strlen(name),
                   (const uint8_t *)peer_name, strlen(peer_name), k,
                   pr->pe, &pr->iterations);
}

static void sae_peer_free(sae_peer *pr)
{
    BN_free(pr->pe);
    BN_clear_free(pr->priv);
    BN_clear_free(pr->mask);
    BN_free(pr->scalar);
    BN_free(pr->element);
    BN_free(pr->peer_scalar);
    BN_free(pr->peer_element);
    BN_clear_free(pr->ss);
    BN_clear_free(pr->kck);
    BN_clear_free(pr->mk);
    memset(pr, 0, sizeof(*pr));
}

/* 固定标量版本：真实实现用随机数生成 private/mask，这里为了跨语言可比性写死。 */
static int sae_peer_commit(sae_peer *pr, const sae_group *gr, const char *priv_hex,
                           const char *mask_hex, BN_CTX *ctx)
{
    pr->priv = BN_new();
    pr->mask = BN_new();
    pr->scalar = BN_new();
    pr->element = BN_new();
    if (pr->priv == NULL || pr->mask == NULL || pr->scalar == NULL ||
        pr->element == NULL) {
        return -1;
    }
    if (BN_hex2bn(&pr->priv, priv_hex) == 0 || BN_hex2bn(&pr->mask, mask_hex) == 0) {
        return -1;
    }
    if (!sae_bn_in_range(pr->priv, gr->q) || !sae_bn_in_range(pr->mask, gr->q)) {
        return -1;                      /* 规范要求 1 < private, mask < q */
    }
    if (sae_commit(gr, pr->pe, pr->priv, pr->mask, pr->scalar, pr->element, ctx) != 0) {
        return -1;
    }
    if (!sae_bn_ge_word(pr->scalar, 2)) {
        return -1;                      /* scalar < 2 必须重来 */
    }
    BN_clear_free(pr->mask);            /* MUST：mask 用完立即销毁 */
    pr->mask = NULL;
    pr->committed = 1;
    return 0;
}

/*
 * 吸收对端提交。返回值即失败原因（自检要按类别分辨，所以不用统一的 -1）：
 *   -1 未 commit；-2 反射攻击；-3 对端标量超范围；-4 对端元素不在子群；-5 内部错误
 */
static int sae_peer_absorb(sae_peer *pr, const sae_group *gr, const sae_peer *peer,
                           BN_CTX *ctx)
{
    if (!pr->committed) {
        return -1;
    }
    if (BN_cmp(peer->scalar, pr->scalar) == 0 &&
        BN_cmp(peer->element, pr->element) == 0) {
        return -2;                      /* 原样回送：反射攻击 */
    }
    if (!sae_bn_in_range(peer->scalar, gr->q)) {
        return -3;
    }
    if (!sae_is_valid_element(gr, peer->element, ctx)) {
        return -4;
    }
    pr->peer_scalar = BN_dup(peer->scalar);
    pr->peer_element = BN_dup(peer->element);
    pr->ss = BN_new();
    pr->kck = BN_new();
    pr->mk = BN_new();
    if (pr->peer_scalar == NULL || pr->peer_element == NULL || pr->ss == NULL ||
        pr->kck == NULL || pr->mk == NULL) {
        return -5;
    }
    if (sae_shared_secret(gr, pr->pe, pr->priv, pr->peer_scalar, pr->peer_element,
                          pr->ss, ctx) != 0) {
        return -5;
    }
    if (sae_derive_keys(gr, pr->ss, pr->kck, pr->mk) != 0) {
        return -5;
    }
    pr->have_keys = 1;
    return 0;
}

/* 自己要发出的确认值（发送方视角：自己的标量/元素在前） */
static int sae_peer_self_confirm(const sae_group *gr, const sae_peer *pr,
                                 uint8_t out[SAE_HASH_LEN])
{
    if (!pr->have_keys) {
        return -1;
    }
    return sae_confirm(gr, pr->kck, pr->scalar, pr->peer_scalar, pr->element,
                       pr->peer_element, (const uint8_t *)pr->name,
                       strlen(pr->name), out);
}

/*
 * 对端**应该**发出的确认值。顺序是「发送方在前、接收方在后」，而这里的发送方是对端，
 * 所以标量/元素的先后与自己 confirm() 时正好相反 —— 把它写成「与自己 confirm() 比较」
 * 是最容易犯的错（Python 版就是这么被自检抓出来的）。
 */
static int sae_peer_expected_confirm(const sae_group *gr, const sae_peer *pr,
                                     uint8_t out[SAE_HASH_LEN])
{
    if (!pr->have_keys) {
        return -1;
    }
    return sae_confirm(gr, pr->kck, pr->peer_scalar, pr->scalar, pr->peer_element,
                       pr->element, (const uint8_t *)pr->peer_name,
                       strlen(pr->peer_name), out);
}

static int sae_peer_verify(const sae_group *gr, const sae_peer *pr,
                           const uint8_t peer_confirm[SAE_HASH_LEN])
{
    uint8_t want[SAE_HASH_LEN];
    if (sae_peer_expected_confirm(gr, pr, want) != 0) {
        return -1;
    }
    return sae_ct_eq(want, peer_confirm, SAE_HASH_LEN);
}

#endif /* SAE_PEER_IMPL_H */
