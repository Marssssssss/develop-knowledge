/*
 * sae_demo.c —— WPA3-SAE（Dragonfly）自检：RFC 7664 的群运算 / 狩猎与啄食 / 提交交换 / 确认交换
 *
 * 没有官方向量（RFC 7664 不提供），所以断言分三类：
 *   A. 可由规范直接推出的等式（PE 必须等于第一轮种子平方、element·PE^scalar == PE^private）；
 *   B. 协议必须成立的性质（两端推出同一 ss/kck、错密码必然确认失败、反射攻击必须中止）；
 *   C. 与 Python / Go 版共享的固定标量向量（比 sha256 摘要，避免在源码里塞 2048 位常量）。
 *
 * 构建（只需编译 .c，两个实现头是文本级包含；本仓库不引入第三方依赖，只用 OpenSSL）：
 *   cc -O2 -Wall -o sae_demo sae_demo.c -lcrypto
 */
#include <stdio.h>
#include <string.h>

#include "sae_check_impl.h"

static void check_commit(const sae_group *gr, BN_CTX *ctx)
{
    sae_peer a;
    char hx[65];
    BIGNUM *t = BN_new(), *lhs = BN_new(), *rhs = BN_new();

    check("初始化 A 端", sae_peer_init(&a, gr, VEC_PW, "alice", "bob", 40) == 0, NULL);
    check("commit 成功", sae_peer_commit(&a, gr, VEC_PRIV_A, VEC_MASK_A, ctx) == 0,
          NULL);
    check("scalar = (private + mask) mod q，与 Python/Go 一致",
          sae_bn_eq_hex(a.scalar, VEC_SCALAR_A), NULL);
    check("scalar >= 2（规范要求）", sae_bn_ge_word(a.scalar, 2), NULL);
    check("element 是合法子群元素", sae_is_valid_element(gr, a.element, ctx), NULL);
    check("element 摘要与 Python/Go 一致",
          sae_bn_sha256_hex(a.element, SAE_P_BYTES, hx) == 0 &&
          strcmp(hx, VEC_ELEM_A_SHA) == 0, hx);

    /* A 类等式：element · PE^scalar == PE^private */
    if (sae_scalar_op(gr, a.scalar, a.pe, t, ctx) == 0 &&
        sae_element_op(gr, a.element, t, lhs, ctx) == 0 &&
        sae_scalar_op(gr, a.priv, a.pe, rhs, ctx) == 0) {
        check("element · PE^scalar == PE^private", BN_cmp(lhs, rhs) == 0, NULL);
    }
    check("element != PE", BN_cmp(a.element, a.pe) != 0, NULL);

    sae_peer_free(&a);
    BN_free(t);
    BN_free(lhs);
    BN_free(rhs);
}

static void check_exchange(const sae_group *gr, BN_CTX *ctx)
{
    sae_peer a, b;
    uint8_t ca[SAE_HASH_LEN], cb[SAE_HASH_LEN];
    char hx[65];

    sae_peer_init(&a, gr, VEC_PW, "alice", "bob", 40);
    sae_peer_init(&b, gr, VEC_PW, "bob", "alice", 40);
    check("两端 PE 相同", BN_cmp(a.pe, b.pe) == 0, NULL);
    sae_peer_commit(&a, gr, VEC_PRIV_A, VEC_MASK_A, ctx);
    sae_peer_commit(&b, gr, VEC_PRIV_B, VEC_MASK_B, ctx);
    check("B 端 scalar 与 Python/Go 一致", sae_bn_eq_hex(b.scalar, VEC_SCALAR_B), NULL);
    check("两端 scalar 不同", BN_cmp(a.scalar, b.scalar) != 0, NULL);
    check("两端互相吸收提交",
          sae_peer_absorb(&a, gr, &b, ctx) == 0 && sae_peer_absorb(&b, gr, &a, ctx) == 0,
          NULL);
    check("两端 ss 相同", BN_cmp(a.ss, b.ss) == 0, NULL);
    check("ss 摘要与 Python/Go 一致",
          sae_bn_sha256_hex(a.ss, SAE_P_BYTES, hx) == 0 && strcmp(hx, VEC_SS_SHA) == 0,
          hx);
    check("两端 kck 相同", BN_cmp(a.kck, b.kck) == 0, NULL);
    check("kck 摘要与 Python/Go 一致",
          sae_bn_sha256_hex(a.kck, SAE_P_BYTES, hx) == 0 && strcmp(hx, VEC_KCK_SHA) == 0,
          hx);
    check("mk 与 kck 不同（KDF 输出被对半切开）", BN_cmp(a.mk, a.kck) != 0, NULL);
    check("两端 mk 相同", BN_cmp(a.mk, b.mk) == 0, NULL);

    check("生成 A 的确认值", sae_peer_self_confirm(gr, &a, ca) == 0, NULL);
    check("生成 B 的确认值", sae_peer_self_confirm(gr, &b, cb) == 0, NULL);
    check("双方确认值不同（发送方标识不同）", sae_ct_eq(ca, cb, SAE_HASH_LEN) == 0, NULL);
    bytes_to_hex(ca, SAE_HASH_LEN, hx);
    check("A 端确认值与 Python/Go 一致", strcmp(hx, VEC_CONFIRM_A) == 0, hx);
    bytes_to_hex(cb, SAE_HASH_LEN, hx);
    check("B 端确认值与 Python/Go 一致", strcmp(hx, VEC_CONFIRM_B) == 0, hx);
    check("B 能验证 A 的确认值", sae_peer_verify(gr, &b, ca) == 1, NULL);
    check("A 能验证 B 的确认值", sae_peer_verify(gr, &a, cb) == 1, NULL);
    /* 顺序是「发送方在前」，拿自己的确认值 verify 自己必然失败 */
    check("拿自己的确认值 verify 自己必然失败",
          sae_peer_verify(gr, &a, ca) == 0 && sae_peer_verify(gr, &b, cb) == 0, NULL);

    sae_peer_free(&a);
    sae_peer_free(&b);
}

static void check_wrong_password(const sae_group *gr, BN_CTX *ctx)
{
    sae_peer a, b;
    uint8_t ca[SAE_HASH_LEN];

    sae_peer_init(&a, gr, VEC_PW, "alice", "bob", 40);
    sae_peer_init(&b, gr, "Password", "bob", "alice", 40);   /* 只差一个大小写 */
    sae_peer_commit(&a, gr, VEC_PRIV_A, VEC_MASK_A, ctx);
    sae_peer_commit(&b, gr, VEC_PRIV_B, VEC_MASK_B, ctx);
    check("大小写不同 → PE 不同", BN_cmp(a.pe, b.pe) != 0, NULL);
    sae_peer_absorb(&a, gr, &b, ctx);
    sae_peer_absorb(&b, gr, &a, ctx);
    check("错密码下 ss 必不相同", BN_cmp(a.ss, b.ss) != 0, NULL);
    sae_peer_self_confirm(gr, &a, ca);
    check("错密码下确认必然失败", sae_peer_verify(gr, &b, ca) == 0, NULL);

    sae_peer_free(&a);
    sae_peer_free(&b);
}

static void check_rejections(const sae_group *gr, BN_CTX *ctx)
{
    sae_peer self, bad;
    uint8_t out[SAE_HASH_LEN];
    BIGNUM *v = BN_new();
    int i;

    /* 1. 反射攻击：对端原样回送自己的 scalar 与 element */
    fresh_peer(&self, gr, ctx);
    memset(&bad, 0, sizeof(bad));
    bad.scalar = BN_dup(self.scalar);
    bad.element = BN_dup(self.element);
    check("反射攻击必须被拒绝", sae_peer_absorb(&self, gr, &bad, ctx) == -2, NULL);
    BN_free(bad.scalar);
    BN_free(bad.element);
    sae_peer_free(&self);

    /* 2. 对端标量不在 (1, q) 内：0、1、q、q+5 */
    for (i = 0; i < 4; i++) {
        memset(&bad, 0, sizeof(bad));
        fresh_peer(&self, gr, ctx);
        if (i == 0) {
            BN_set_word(v, 0);
        } else if (i == 1) {
            BN_set_word(v, 1);
        } else {
            BN_copy(v, gr->q);
            if (i == 3) {
                BN_add_word(v, 5);
            }
        }
        bad.scalar = BN_dup(v);
        bad.element = BN_dup(self.element);
        check("非法对端标量必须被拒绝",
              sae_peer_absorb(&self, gr, &bad, ctx) == -3, NULL);
        BN_free(bad.scalar);
        BN_free(bad.element);
        sae_peer_free(&self);
    }

    /* 3. 对端元素不在子群内：0、1、p-1、p（标量取另一组固定值以免撞上反射判定） */
    for (i = 0; i < 4; i++) {
        memset(&bad, 0, sizeof(bad));
        fresh_peer(&self, gr, ctx);
        BN_hex2bn(&bad.scalar, VEC_SCALAR_B);
        if (i == 0) {
            BN_set_word(v, 0);
        } else if (i == 1) {
            BN_set_word(v, 1);
        } else if (i == 2) {
            BN_copy(v, gr->p);
            BN_sub_word(v, 1);
        } else {
            BN_copy(v, gr->p);
        }
        bad.element = BN_dup(v);
        check("非法对端元素必须被拒绝",
              sae_peer_absorb(&self, gr, &bad, ctx) == -4, NULL);
        BN_free(bad.scalar);
        BN_free(bad.element);
        sae_peer_free(&self);
    }

    /* 4. 还没 commit 就想吸收对端提交 */
    memset(&bad, 0, sizeof(bad));
    sae_peer_init(&self, gr, VEC_PW, "alice", "bob", 1);
    bad.scalar = BN_new();
    bad.element = BN_new();
    BN_set_word(bad.scalar, 5);
    BN_set_word(bad.element, 5);
    check("未 commit 就 absorb 必须报错", sae_peer_absorb(&self, gr, &bad, ctx) == -1,
          NULL);
    check("未 commit 就不能出确认值", sae_peer_self_confirm(gr, &self, out) == -1, NULL);
    check("没有 kck 时 verify 也失败", sae_peer_verify(gr, &self, out) == -1, NULL);
    BN_free(bad.scalar);
    BN_free(bad.element);
    sae_peer_free(&self);
    BN_free(v);
}

static void check_sensitive(const sae_group *gr, BN_CTX *ctx)
{
    sae_peer a;
    BIGNUM *s = BN_new();

    /* mask 与 kck 都靠 BN_clear_free 销毁：先确认它真的把内存改掉了 */
    BN_hex2bn(&s, VEC_MASK_A);
    check("清空前非零", !BN_is_zero(s), NULL);
    BN_clear(s);
    check("BN_clear 后归零（mask 唯一能反推 PE 的值）", BN_is_zero(s), NULL);
    BN_free(s);

    check("commit 后 mask 指针已置空",
          sae_peer_init(&a, gr, VEC_PW, "alice", "bob", 1) == 0 &&
          sae_peer_commit(&a, gr, VEC_PRIV_A, VEC_MASK_A, ctx) == 0 &&
          a.mask == NULL, NULL);
    sae_peer_free(&a);
}

int main(void)
{
    sae_group gr;
    BN_CTX *ctx = BN_CTX_new();

    printf("WPA3 SAE / Dragonfly 自检 —— RFC 7664 + RFC 3526 §3（OpenSSL BIGNUM）\n");
    if (ctx == NULL || sae_group_init(&gr) != 0) {
        printf("初始化失败：需要 OpenSSL 3.0+ 的 libcrypto\n");
        return 1;
    }
    check_group(&gr, ctx);
    check_pwe(&gr, ctx);
    check_commit(&gr, ctx);
    check_exchange(&gr, ctx);
    check_wrong_password(&gr, ctx);
    check_rejections(&gr, ctx);
    check_sensitive(&gr, ctx);
    sae_group_free(&gr);
    BN_CTX_free(ctx);

    printf("sae_demo: %d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? 0 : 1;
}