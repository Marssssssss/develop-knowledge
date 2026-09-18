/*
 * sae_exchange_impl.h —— Dragonfly 的提交/确认交换（RFC 7664 §3.3 / §3.4）
 *
 * 依赖 sae_group_impl.h 的群算子与 KDF；两个头一起被 sae_demo.c 文本级包含。
 *
 * 三条规范 MUST 在 sae_demo.c 的自检里逐条验证：
 *   1. scalar < 2 必须重来；private / mask 必须落在 (1, q) 内
 *   2. mask 用完立即销毁（它是唯一能把 Element 反推回 PE 的值）
 *   3. 对端回送与自己完全相同的 scalar 与 Element 属于反射攻击，必须中止
 */
#ifndef SAE_EXCHANGE_IMPL_H
#define SAE_EXCHANGE_IMPL_H

#include "sae_group_impl.h"

/* ------------------------------------------------------------ 提交交换 */

/*
 * scalar = (private + mask) mod q；Element = inverse(mask ⋅ PE) = PE^(-mask)
 * mask 用完后调用方必须立即 BN_clear_free（它是唯一能把 Element 还原成 PE 的值）。
 */
static int sae_commit(const sae_group *gr, const BIGNUM *pe, const BIGNUM *priv,
                      const BIGNUM *mask, BIGNUM *scalar, BIGNUM *element,
                      BN_CTX *ctx)
{
    BIGNUM *tmp = BN_new();
    int rc = -1;
    if (tmp == NULL) {
        return -1;
    }
    if (BN_mod_add(scalar, priv, mask, gr->q, ctx) != 1) {
        goto done;
    }
    if (sae_scalar_op(gr, mask, pe, tmp, ctx) != 0) {
        goto done;
    }
    if (sae_inverse(gr, tmp, element, ctx) != 0) {
        goto done;
    }
    rc = 0;
done:
    BN_free(tmp);
    return rc;
}

/* ss = (peer-Element · PE^peer-scalar)^private mod p */
static int sae_shared_secret(const sae_group *gr, const BIGNUM *pe,
                             const BIGNUM *priv, const BIGNUM *peer_scalar,
                             const BIGNUM *peer_element, BIGNUM *ss, BN_CTX *ctx)
{
    BIGNUM *inner = BN_new(), *t = BN_new();
    int rc = -1;
    if (inner == NULL || t == NULL) {
        goto done;
    }
    if (sae_scalar_op(gr, peer_scalar, pe, t, ctx) != 0) {
        goto done;
    }
    if (sae_element_op(gr, peer_element, t, inner, ctx) != 0) {
        goto done;
    }
    if (sae_scalar_op(gr, priv, inner, ss, ctx) != 0) {
        goto done;
    }
    rc = 0;
done:
    BN_free(inner);
    BN_free(t);
    return rc;
}

/*
 * kck | mk = KDF(ss, "Dragonfly Key Derivation")，长度 2*len(p) 位，各取一半。
 * ss 是群元素，必须按 len(p) 定宽序列化 —— 变长会让 KDF 的输入串有歧义。
 */
static int sae_derive_keys(const sae_group *gr, const BIGNUM *ss, BIGNUM *kck,
                           BIGNUM *mk)
{
    uint8_t ss_bytes[SAE_P_BYTES];
    uint8_t material[SAE_P_BYTES * 2];      /* 2 * 2048 位 */
    if (BN_bn2binpad(ss, ss_bytes, SAE_P_BYTES) != SAE_P_BYTES) {
        return -1;
    }
    if (sae_kdf(SAE_KEY_LABEL, ss_bytes, sizeof(ss_bytes), material,
                sizeof(material)) != 0) {
        return -1;
    }
    if (BN_bin2bn(material, SAE_P_BYTES, kck) == NULL) {
        return -1;
    }
    if (BN_bin2bn(material + SAE_P_BYTES, SAE_P_BYTES, mk) == NULL) {
        return -1;
    }
    return 0;
}

/*
 * confirm = H(kck | 发送方标量 | 接收方标量 | 发送方元素 | 接收方元素 | 发送方标识)
 * 数值一律定宽大端（BN_bn2binpad），否则不同组合会撞出同一输入串。
 */
static int sae_confirm(const sae_group *gr, const BIGNUM *kck, const BIGNUM *scalar,
                       const BIGNUM *peer_scalar, const BIGNUM *element,
                       const BIGNUM *peer_element, const uint8_t *sender,
                       size_t sender_len, uint8_t out[SAE_HASH_LEN])
{
    uint8_t buf[SAE_P_BYTES * 3 + 64 + 64];
    int q_bytes = BN_num_bytes(gr->q);
    size_t n = 0;
    if (BN_bn2binpad(kck, buf, SAE_P_BYTES) != SAE_P_BYTES) {
        return -1;
    }
    n = SAE_P_BYTES;
    if (BN_bn2binpad(scalar, buf + n, q_bytes) != q_bytes) {
        return -1;
    }
    n += (size_t)q_bytes;
    if (BN_bn2binpad(peer_scalar, buf + n, q_bytes) != q_bytes) {
        return -1;
    }
    n += (size_t)q_bytes;
    if (BN_bn2binpad(element, buf + n, SAE_P_BYTES) != SAE_P_BYTES) {
        return -1;
    }
    n += SAE_P_BYTES;
    if (BN_bn2binpad(peer_element, buf + n, SAE_P_BYTES) != SAE_P_BYTES) {
        return -1;
    }
    n += SAE_P_BYTES;
    if (n + sender_len > sizeof(buf)) {
        return -1;
    }
    memcpy(buf + n, sender, sender_len);
    n += sender_len;
    return sae_sha256(buf, n, out);
}

#endif /* SAE_EXCHANGE_IMPL_H */
