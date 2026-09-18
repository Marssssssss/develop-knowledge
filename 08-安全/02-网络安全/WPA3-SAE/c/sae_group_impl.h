/*
 * sae_group_impl.h —— Dragonfly（WPA3-SAE）群参数 / KDF / 密码元素（RFC 7664 §2.2 §3.2）
 *
 * 2048 位模幂用 OpenSSL BIGNUM；群参数取自 RFC 3526 §3 的 2048 位 MODP Group（id 14）：
 * p 是安全素数，G = 2，q = (p-1)/2。H = SHA-256，KDF = HKDF-SHA256（全零盐，标签作 info）。
 */
#ifndef SAE_GROUP_IMPL_H
#define SAE_GROUP_IMPL_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <openssl/bn.h>
#include <openssl/hmac.h>
#include <openssl/sha.h>


/* RFC 3526 §3（2048-bit MODP Group, id 14）的素数，逐段拼接便于与原 RFC 对照 */
#define SAE_P_HEX \
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74" \
    "020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437" \
    "4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED" \
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05" \
    "98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB" \
    "9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B" \
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718" \
    "3995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF"

#define SAE_PWE_LABEL "Dragonfly Hunting And Pecking"
#define SAE_KEY_LABEL "Dragonfly Key Derivation"
#define SAE_HASH_LEN 32
#define SAE_P_BYTES 256                 /* 2048 位 = 256 字节 */

typedef struct {
    BIGNUM *p;
    BIGNUM *q;                          /* 子群阶 (p-1)/2 */
    BIGNUM *g;                          /* 生成元 2 */
    BIGNUM *exp_peck;                   /* (p-1)/q，安全素数下恒为 2 */
} sae_group;

static void sae_group_free(sae_group *gr);

/* ---------------------------------------------------------------- 基础 */

static int sae_sha256(const uint8_t *in, size_t len, uint8_t out[SAE_HASH_LEN])
{
    if (SHA256(in, len, out) == NULL) {
        return -1;
    }
    return 0;
}

static int sae_hmac256(const uint8_t *key, size_t key_len, const uint8_t *data,
                       size_t data_len, uint8_t out[SAE_HASH_LEN])
{
    unsigned int n = 0;
    if (HMAC(EVP_sha256(), key, (int)key_len, data, data_len, out, &n) == NULL) {
        return -1;
    }
    return n == SAE_HASH_LEN ? 0 : -1;
}

/* HKDF-Expand：T(i) = HMAC(PRK, T(i-1) | info | i) */
static int sae_kdf_expand(const uint8_t *prk, const uint8_t *info, size_t info_len,
                          uint8_t *out, size_t out_len)
{
    uint8_t t[SAE_HASH_LEN];
    uint8_t buf[SAE_HASH_LEN + 512 + 1];
    size_t t_len = 0, done = 0;
    uint8_t ctr = 1;
    if (info_len + SAE_HASH_LEN + 1 > sizeof(buf) || out_len > 255 * SAE_HASH_LEN) {
        return -1;
    }
    while (done < out_len) {
        size_t take;
        memcpy(buf, t, t_len);
        if (info_len > 0) {
            memcpy(buf + t_len, info, info_len);
        }
        buf[t_len + info_len] = ctr;
        if (sae_hmac256(prk, SAE_HASH_LEN, buf, t_len + info_len + 1, t) != 0) {
            return -1;
        }
        t_len = SAE_HASH_LEN;
        take = (out_len - done < SAE_HASH_LEN) ? out_len - done : SAE_HASH_LEN;
        memcpy(out + done, t, take);
        done += take;
        ctr++;
    }
    return 0;
}

/* KDF-n(label, ikm) = HKDF-SHA256（Extract 盐为全零） */
static int sae_kdf(const char *label, const uint8_t *ikm, size_t ikm_len,
                   uint8_t *out, size_t out_len)
{
    uint8_t zeros[SAE_HASH_LEN], prk[SAE_HASH_LEN];
    memset(zeros, 0, sizeof(zeros));
    if (sae_hmac256(zeros, sizeof(zeros), ikm, ikm_len, prk) != 0) {
        return -1;
    }
    return sae_kdf_expand(prk, (const uint8_t *)label, strlen(label), out, out_len);
}

/* ---------------------------------------------------------------- 群参数 */

static int sae_group_init(sae_group *gr)
{
    BN_CTX *ctx;
    BIGNUM *pm1 = NULL;
    int ok = 0;

    gr->p = gr->q = gr->g = gr->exp_peck = NULL;
    ctx = BN_CTX_new();
    if (ctx == NULL) {
        return -1;
    }
    gr->p = BN_new();
    gr->q = BN_new();
    gr->g = BN_new();
    gr->exp_peck = BN_new();
    pm1 = BN_new();
    if (gr->p == NULL || gr->q == NULL || gr->g == NULL ||
        gr->exp_peck == NULL || pm1 == NULL) {
        goto done;
    }
    if (BN_hex2bn(&gr->p, SAE_P_HEX) == 0 || BN_set_word(gr->g, 2) != 1) {
        goto done;
    }
    if (BN_copy(gr->q, gr->p) == NULL || BN_sub_word(gr->q, 1) != 1 ||
        BN_rshift1(gr->q, gr->q) != 1) {          /* q = (p-1)/2 */
        goto done;
    }
    /* exp_peck = (p-1)/q，安全素数下恰为 2 */
    if (BN_copy(pm1, gr->p) == NULL || BN_sub_word(pm1, 1) != 1 ||
        BN_div(gr->exp_peck, NULL, pm1, gr->q, ctx) != 1) {
        goto done;
    }
    ok = 1;
done:
    BN_free(pm1);
    BN_CTX_free(ctx);
    if (!ok) {
        sae_group_free(gr);                       /* 半成品一律清干净 */
        return -1;
    }
    return 0;
}

static void sae_group_free(sae_group *gr)
{
    BN_free(gr->p);
    BN_free(gr->q);
    BN_free(gr->g);
    BN_free(gr->exp_peck);
}

/* Z = Y^x mod p */
static int sae_scalar_op(const sae_group *gr, const BIGNUM *x, const BIGNUM *y,
                         BIGNUM *z, BN_CTX *ctx)
{
    return BN_mod_exp(z, y, x, gr->p, ctx) == 1 ? 0 : -1;
}

/* Z = X*Y mod p */
static int sae_element_op(const sae_group *gr, const BIGNUM *x, const BIGNUM *y,
                          BIGNUM *z, BN_CTX *ctx)
{
    return BN_mod_mul(z, x, y, gr->p, ctx) == 1 ? 0 : -1;
}

/* R * R^-1 mod p = 1 */
static int sae_inverse(const sae_group *gr, const BIGNUM *r, BIGNUM *out,
                       BN_CTX *ctx)
{
    return BN_mod_inverse(out, r, gr->p, ctx) != NULL ? 0 : -1;
}

/* RFC 7664 §2.2：1 < e < p-1 且 e^q mod p == 1 */
static int sae_is_valid_element(const sae_group *gr, const BIGNUM *e, BN_CTX *ctx)
{
    BIGNUM *one = BN_new(), *pm1 = BN_new(), *t = BN_new();
    int ok = 0;
    if (one == NULL || pm1 == NULL || t == NULL) {
        goto done;
    }
    if (BN_set_word(one, 1) != 1 || BN_copy(pm1, gr->p) == NULL ||
        BN_sub_word(pm1, 1) != 1) {
        goto done;
    }
    if (BN_cmp(e, one) <= 0 || BN_cmp(e, pm1) >= 0) {
        goto done;                       /* 必须严格在 (1, p-1) 内 */
    }
    if (BN_mod_exp(t, e, gr->q, gr->p, ctx) != 1) {
        goto done;
    }
    ok = BN_is_one(t);
done:
    BN_free(one);
    BN_free(pm1);
    BN_free(t);
    return ok;
}

/* ------------------------------------------ 密码元素：狩猎与啄食（§3.2） */
/*
 * base = H(max(A,B)|min(A,B)|password|counter)，seed = KDF-(len(p)+64)(base) mod (p-1) + 1，
 * temp = seed^((p-1)/q) mod p（安全素数下就是 seed^2）。找到后仍跑满 k 轮，
 * 掩盖真实迭代数 —— 这是 §3.2 明写的抗侧信道要求。
 */
static int sae_pwe(const sae_group *gr, const uint8_t *pw, size_t pw_len,
                   const uint8_t *ida, size_t ida_len,
                   const uint8_t *idb, size_t idb_len, int k,
                   BIGNUM *pe, int *iterations)
{
    BN_CTX *ctx = BN_CTX_new();
    uint8_t base[SAE_HASH_LEN], kdf_out[SAE_P_BYTES + 8];
    uint8_t buf[512];
    const uint8_t *hi = ida, *lo = idb;
    size_t hi_len = ida_len, lo_len = idb_len;
    BIGNUM *seed = NULL, *temp = NULL, *pm1 = NULL;
    size_t n_bytes = (2048 + 64 + 7) / 8;      /* len(p) + 64 位 */
    int found = 0, counter = 1, ok = -1;

    if (ctx == NULL) {
        return -1;
    }
    seed = BN_new();
    temp = BN_new();
    pm1 = BN_new();
    if (seed == NULL || temp == NULL || pm1 == NULL) {
        goto done;
    }
    if (BN_copy(pm1, gr->p) == NULL || BN_sub_word(pm1, 1) != 1) {
        goto done;
    }
    /* 身份按 max/min 排序后拼接：双方视角必须算出同一个 PE（字节序比较，短串更小） */
    {
        size_t m = (ida_len < idb_len) ? ida_len : idb_len;
        int c = memcmp(ida, idb, m);
        int a_is_hi = (c > 0) || (c == 0 && ida_len >= idb_len);
        if (!a_is_hi) {
            hi = idb;
            hi_len = idb_len;
            lo = ida;
            lo_len = ida_len;
        }
    }
    if (hi_len + lo_len + pw_len + 1 > sizeof(buf)) {
        goto done;
    }
    while (1) {
        size_t n = 0;
        memcpy(buf, hi, hi_len);
        n += hi_len;
        memcpy(buf + n, lo, lo_len);
        n += lo_len;
        memcpy(buf + n, pw, pw_len);
        n += pw_len;
        buf[n++] = (uint8_t)counter;
        if (sae_sha256(buf, n, base) != 0) {
            goto done;
        }
        if (sae_kdf(SAE_PWE_LABEL, base, sizeof(base), kdf_out, n_bytes) != 0) {
            goto done;
        }
        if (BN_bin2bn(kdf_out, (int)n_bytes, seed) == NULL ||
            BN_mod(seed, seed, pm1, ctx) != 1 || BN_add_word(seed, 1) != 1) {
            goto done;
        }
        if (sae_scalar_op(gr, gr->exp_peck, seed, temp, ctx) != 0) {
            goto done;
        }
        if (BN_is_one(temp) == 0 && BN_is_zero(temp) == 0 && !found) {
            if (BN_copy(pe, temp) == NULL) {
                goto done;
            }
            found = 1;
        }
        counter++;
        if (found && counter > k) {
            break;
        }
    }
    if (iterations != NULL) {
        *iterations = counter - 1;
    }
    ok = 0;
done:
    if (ctx != NULL) {
        BN_CTX_free(ctx);
    }
    BN_free(seed);
    BN_free(temp);
    BN_free(pm1);
    return ok;
}

#endif /* SAE_GROUP_IMPL_H */
