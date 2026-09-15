/* ecdsa_test.c — ECDSA(P-256)+RFC 6979 自测主程序(向量见 RFC 6979 A.2.5)
 * 编译: cc ecdsa.c bn256.c hmacsha256.c ecdsa_test.c -o ecdsa  (需 __int128) */
#include <stdio.h>
#include <string.h>
#include "bn256.h"
#include "hmacsha256.h"
#include "ecdsa.h"

int main(void) {
    int fail = 0;
    p256_init();
    pt G;
    memcpy(G.x, Gx, 32);
    memcpy(G.y, Gy, 32);
    G.inf = 0;

    /* demo1: 曲线自检 + n·G = 无穷远点 */
    {
        pt inf;
        pt_mul(N, &G, &inf);
        if (!on_curve(&G) || !inf.inf) {
            puts("FAIL demo1 曲线自检");
            fail++;
        } else {
            puts("demo1 曲线/基点/阶自检: PASS");
        }
    }

    /* demo2: RFC 6979 A.2.5 */
    uint64_t x[4], r[4], s[4], k[4];
    pt Q;
    uint8_t h1[32], K[32], V[32];
    bn_hex2limb("C9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721", x);
    pt_mul(x, &G, &Q);
    if (!hexeq(Q.x, "60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6") ||
        !hexeq(Q.y, "7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299")) {
        puts("FAIL demo2 公钥向量");
        fail++;
    }
    sha256((const uint8_t *)"sample", 6, h1);
    drbg_init(x, h1, K, V);
    drbg_gen(K, V, k);
    if (!hexeq(k, "A6E3C57DD01ABE90086538398355DD4C3B17AA873382B0F24D6129493D8AAD60")) {
        printf("FAIL demo2 k(sample)=");
        bn_print(k);
        fail++;
    }
    sign(x, (const uint8_t *)"sample", 6, r, s);
    if (!hexeq(r, "EFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716") ||
        !hexeq(s, "F7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8")) {
        printf("FAIL demo2 sample 签名 r=");
        bn_print(r);
        printf("                 s=");
        bn_print(s);
        fail++;
    }
    sign(x, (const uint8_t *)"test", 4, r, s);
    if (!hexeq(r, "F1ABB023518351CD71D881567B1EA663ED3EFCF6C5132B354F28D3B0B7D38367") ||
        !hexeq(s, "019F4113742A2B14BD25926B49C649155F267E60D3814B4C0CC84250E46F0083")) {
        puts("FAIL demo2 test 签名");
        fail++;
    }
    if (!fail)
        puts("demo2 RFC 6979 A.2.5 向量(sample/test): PASS");

    /* demo3: 验证与篡改 */
    sign(x, (const uint8_t *)"sample", 6, r, s);
    if (!verify(&Q, (const uint8_t *)"sample", 6, r, s) ||
        verify(&Q, (const uint8_t *)"sample!", 7, r, s)) {
        puts("FAIL demo3 验证/篡改");
        fail++;
    } else {
        puts("demo3 签名验证 + 篡改检测: PASS");
    }

    /* demo4: 确定性与私钥雪崩 */
    {
        uint64_t r2[4], s2[4], xb[4], r3[4], s3[4];
        sign(x, (const uint8_t *)"again", 5, r, s);
        sign(x, (const uint8_t *)"again", 5, r2, s2);
        xb[0] = x[0] ^ 1; /* 篡改 1 bit 私钥 */
        memcpy(xb + 1, x + 1, 24);
        sign(xb, (const uint8_t *)"again", 5, r3, s3);
        if (bn_cmp(r, r2) || bn_cmp(s, s2) || !bn_cmp(r, r3)) {
            puts("FAIL demo4 确定性/雪崩");
            fail++;
        } else {
            puts("demo4 确定性 + 私钥雪崩: PASS");
        }
    }

    /* demo5: 20 组 round-trip(d = SHA256("keyN") mod n)
     * 第二个 verify 用 n2+1 长度, 把 snprintf 的结尾 NUL 计入 —— 消息确实不同 */
    {
        int ok = 1;
        char key[16], msg[24];
        for (int i = 0; i < 20 && ok; i++) {
            uint8_t h[32];
            uint64_t d[4], rr[4], ss[4];
            pt Qd;
            int n1 = snprintf(key, sizeof(key), "key%d", i);
            int n2 = snprintf(msg, sizeof(msg), "message-%d", i);
            sha256((uint8_t *)key, n1, h);
            bn_be2limb(h, d);
            mod_n(d, d);
            pt_mul(d, &G, &Qd);
            sign(d, (uint8_t *)msg, n2, rr, ss);
            if (!verify(&Qd, (uint8_t *)msg, n2, rr, ss) ||
                verify(&Qd, (uint8_t *)msg, n2 + 1, rr, ss)) {
                printf("FAIL demo5 第 %d 组\n", i);
                fail++;
                ok = 0;
            }
        }
        if (ok)
            puts("demo5 随机 20 组 round-trip: PASS");
    }
    if (fail) {
        puts("SOME FAILED");
        return 1;
    }
    puts("ALL PASS");
    return 0;
}
