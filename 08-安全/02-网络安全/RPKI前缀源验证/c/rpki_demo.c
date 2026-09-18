/*
 * rpki_demo.c —— RPKI 前缀源验证的可执行自检（C）
 *
 * 构建：
 *   gcc -O2 -Wall -Wextra -o rpki_demo rpki_demo.c
 *
 * 期望值来源：
 *   RFC 6482 §3.3  203.0.113/24 + maxLength 26 的授权范围（/24 与 /25 可以、/27 不行）
 *   RFC 6482 §3.3  maxLength 的取值范围与「缺省只授权精确前缀」
 *   RFC 6811 §2.1  三态验证过程
 *   RFC 6811 §2    Route Origin ASN 的四种来源
 *   RFC 6482 §4    内容类型 OID 1.2.840.113549.1.9.16.1.24 的 DER 编码
 *   RFC 3779       IPAddress 是 BIT STRING，内容左对齐、低位补零
 */
#include <stdio.h>
#include <string.h>

#include "rpki_impl.h"

static int g_checks = 0;

static void check(const char *what, int cond)
{
    if (!cond) {
        printf("  FAIL: %s\n", what);
    }
    g_checks += cond ? 1 : 0;
}

static rpki_prefix pfx(const char *text)
{
    static rpki_prefix p;
    if (rpki_prefix_parse4(&p, text) != 0) {
        printf("  FAIL: 无法解析 %s\n", text);
    }
    return p;
}

/* RFC 6482 §3.3 的例子：一条 203.0.113.0/24 + maxLength 26 的 VRP */
static rpki_vrp sample_vrp(void)
{
    rpki_vrp v;
    v.prefix = pfx("203.0.113.0/24");
    v.max_length = 26;
    v.asn = 64496;
    return v;
}

static void test_prefix(int *n)
{
    rpki_prefix p = pfx("203.0.113.0/24");
    check("解析出 4 个字节", p.addr[0] == 203 && p.addr[1] == 0 && p.addr[2] == 113 &&
          p.addr[3] == 0);
    check("af / 长度", p.afi == RPKI_IPV4 && p.length == 24);
    check("无斜杠默认为 /32", pfx("10.1.2.3").length == 32);
    check("非法前缀被拒绝", rpki_prefix_parse4(&p, "203.0.113.999/24") != 0 &&
          rpki_prefix_parse4(&p, "1.2.3/24") != 0 &&
          rpki_prefix_parse4(&p, "203.0.113.0/33") != 0);
    (*n) += 4;

    check("覆盖更具体", rpki_prefix_contains(&p, &(rpki_prefix){.afi = RPKI_IPV4,
          .length = 26, .addr = {203, 0, 113, 0}}));
    check("不覆盖另一半", !rpki_prefix_contains(&p, &(rpki_prefix){.afi = RPKI_IPV4,
          .length = 26, .addr = {203, 0, 113, 128}}));
    check("不覆盖更短", !rpki_prefix_contains(&p, &(rpki_prefix){.afi = RPKI_IPV4,
          .length = 16, .addr = {203, 0, 0, 0}}));
    /* RFC 6811 §2：位比较只到 VRP 前缀长度为止，主机位不参与 */
    check("按位比较忽略主机位", rpki_prefix_contains(&(rpki_prefix){.afi = RPKI_IPV4,
          .length = 25, .addr = {203, 0, 113, 0}}, &(rpki_prefix){.afi = RPKI_IPV4,
          .length = 26, .addr = {203, 0, 113, 64}}));
    (*n) += 4;

    /* 跨地址族：v6 的 VRP 不可能覆盖 v4 的路由 */
    rpki_prefix v6;
    rpki_prefix_make(&v6, RPKI_IPV6, NULL, 32);
    check("跨族不覆盖", !rpki_prefix_contains(&v6, &p));
    check("v6 位数是 128", rpki_prefix_bits(RPKI_IPV6) == 128);
    (*n) += 2;
}

static void test_bitstring(int *n)
{
    uint8_t buf[20];
    size_t len;
    /* 注意：结构体返回值是右值，不能直接取地址 —— 必须先落到局部变量 */
    rpki_prefix p24 = pfx("203.0.113.0/24");
    rpki_prefix p26 = pfx("203.0.113.0/26");
    rpki_prefix p26b = pfx("203.0.113.192/26");
    rpki_prefix host = pfx("203.0.113.1/24");

    /* /24：未使用 0 位，内容 203.0.113 = CB 00 71 */
    len = rpki_prefix_bitstring(&p24, buf);
    check("/24 的 BIT STRING", len == 4 && buf[0] == 0 && buf[1] == 0xCB &&
          buf[2] == 0x00 && buf[3] == 0x71);
    /* /26：未使用 6 位，内容左对齐后是 CB 00 71 00 */
    len = rpki_prefix_bitstring(&p26, buf);
    check("/26 未使用位数为 6", len == 5 && buf[0] == 6);
    check("/26 内容左对齐", buf[1] == 0xCB && buf[2] == 0x00 && buf[3] == 0x71 &&
          buf[4] == 0x00);
    /* 主机位必须清零：203.0.113.1/24 与 203.0.113.0/24 的编码必须一致 */
    len = rpki_prefix_bitstring(&host, buf);
    check("主机位被清零", len == 4 && buf[1] == 0xCB && buf[2] == 0x00 &&
          buf[3] == 0x71);
    (*n) += 4;

    /* 长度不是 8 的倍数时左对齐才正确：右移后取字节会得到 03 2C 01 C4 */
    len = rpki_prefix_bitstring(&p26b, buf);
    check("/26 上半个 C0", len == 5 && buf[0] == 6 && buf[4] == 0xC0);
    (*n) += 1;
}

static void test_max_length(int *n)
{
    rpki_prefix p = pfx("203.0.113.0/24");
    check("maxLength 26 合法", rpki_max_length_ok(&p, 1, 26));
    check("maxLength 等于前缀长度合法", rpki_max_length_ok(&p, 1, 24));
    check("maxLength 小于前缀长度非法", !rpki_max_length_ok(&p, 1, 23));
    check("maxLength 超过 AFI 位宽非法", !rpki_max_length_ok(&p, 1, 33));
    check("缺省 maxLength 合法", rpki_max_length_ok(&p, 0, 0));
    (*n) += 5;
}

static void test_validate(int *n)
{
    rpki_vrp vrp = sample_vrp();
    rpki_vrp zero = sample_vrp();
    rpki_vrp double_vrp[2];
    zero.asn = 0;                                  /* AS 0：保留值，永不 Matched */

    check("精确前缀 Valid",
          rpki_validate(&vrp, 1, &(rpki_prefix){.afi = RPKI_IPV4, .length = 24,
                        .addr = {203, 0, 113, 0}}, 64496) == RPKI_VALID);
    check("任意 /26 都 Valid",
          rpki_validate(&vrp, 1, &(rpki_prefix){.afi = RPKI_IPV4, .length = 26,
                        .addr = {203, 0, 113, 64}}, 64496) == RPKI_VALID);
    check("过长 Invalid",
          rpki_validate(&vrp, 1, &(rpki_prefix){.afi = RPKI_IPV4, .length = 27,
                        .addr = {203, 0, 113, 0}}, 64496) == RPKI_INVALID);
    check("未被覆盖 NotFound",
          rpki_validate(&vrp, 1, &(rpki_prefix){.afi = RPKI_IPV4, .length = 24,
                        .addr = {203, 0, 114, 0}}, 64496) == RPKI_NOT_FOUND);
    check("源 AS 不符 Invalid",
          rpki_validate(&vrp, 1, &(rpki_prefix){.afi = RPKI_IPV4, .length = 24,
                        .addr = {203, 0, 113, 0}}, 65000) == RPKI_INVALID);
    check("源 AS 为 NONE(0) Invalid",
          rpki_validate(&vrp, 1, &(rpki_prefix){.afi = RPKI_IPV4, .length = 24,
                        .addr = {203, 0, 113, 0}}, 0) == RPKI_INVALID);
    check("AS 0 的 VRP 永远不 Matched",
          rpki_validate(&zero, 1, &(rpki_prefix){.afi = RPKI_IPV4, .length = 24,
                        .addr = {203, 0, 113, 0}}, 0) == RPKI_INVALID);
    (*n) += 7;

    check("状态名", strcmp(rpki_state_name(RPKI_VALID), "Valid") == 0 &&
          strcmp(rpki_state_name(RPKI_INVALID), "Invalid") == 0 &&
          strcmp(rpki_state_name(RPKI_NOT_FOUND), "NotFound") == 0);
    (*n) += 1;

    /* 多条 VRP：只要有一条 Matched 就是 Valid，与遍历顺序无关 */
    double_vrp[0] = sample_vrp();
    double_vrp[0].max_length = 24;
    double_vrp[1] = sample_vrp();
    double_vrp[1].asn = 65000;
    check("列表顺序不影响结果",
          rpki_validate(double_vrp, 2, &(rpki_prefix){.afi = RPKI_IPV4, .length = 25,
                        .addr = {203, 0, 113, 0}}, 65000) == RPKI_VALID);
    /* 只有 64496 的 VRP 覆盖到 /25 时，65000 发起的公告就是 Invalid */
    check("另一条不匹配则 Invalid",
          rpki_validate(&double_vrp[0], 1, &(rpki_prefix){.afi = RPKI_IPV4, .length = 25,
                        .addr = {203, 0, 113, 0}}, 65000) == RPKI_INVALID);
    /* 空 VRP 库 → 全部 NotFound */
    check("空库 NotFound",
          rpki_validate(NULL, 0, &(rpki_prefix){.afi = RPKI_IPV4, .length = 24,
                        .addr = {203, 0, 113, 0}}, 64496) == RPKI_NOT_FOUND);
    (*n) += 3;
}

static void test_origin(int *n)
{
    uint8_t types[3];
    uint32_t lasts[3];

    types[0] = RPKI_AS_SEQUENCE; lasts[0] = 65002;
    check("AS_SEQUENCE 取最右 AS", rpki_origin_asn(types, lasts, 1, 65000) == 65002);
    check("空 AS_PATH 取本机 AS", rpki_origin_asn(types, lasts, 0, 65000) == 65000);
    types[0] = RPKI_AS_CONFED_SEQUENCE; lasts[0] = 64512;
    check("联邦段取本机 AS", rpki_origin_asn(types, lasts, 1, 65000) == 65000);
    types[0] = RPKI_AS_SEQUENCE; lasts[0] = 65001;
    types[1] = RPKI_AS_SET; lasts[1] = 65003;
    check("末段 AS_SET → NONE", rpki_origin_asn(types, lasts, 2, 65000) == 0);
    (*n) += 4;
}

static void test_oid(int *n)
{
    /* RFC 6482 §4 的 id-ct-routeOriginAuthz 与常见的 SHA-256withRSA OID */
    const unsigned roa[] = {1, 2, 840, 113549, 1, 9, 16, 1, 24};
    const unsigned rsa[] = {1, 2, 840, 113549, 1, 1, 11};
    uint8_t out[16];
    size_t len = rpki_oid_encode(roa, sizeof(roa) / sizeof(roa[0]), out);
    const uint8_t expect[] = {0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x09,
                              0x10, 0x01, 0x18};
    check("ROA 内容类型 OID 长度 11", len == sizeof(expect));
    check("ROA 内容类型 OID 字节", memcmp(out, expect, sizeof(expect)) == 0);
    len = rpki_oid_encode(rsa, sizeof(rsa) / sizeof(rsa[0]), out);
    check("sha256WithRSAEncryption OID",
          len == 9 && memcmp(out, "\x2a\x86\x48\x86\xf7\x0d\x01\x01\x0b", 9) == 0);
    (*n) += 3;
}

int main(void)
{
    int n;

    n = 0; test_prefix(&n);
    printf("  前缀解析与覆盖判断:     %d checks\n", n);
    n = 0; test_bitstring(&n);
    printf("  BIT STRING 左对齐编码:  %d checks\n", n);
    n = 0; test_max_length(&n);
    printf("  maxLength 取值约束:     %d checks\n", n);
    n = 0; test_validate(&n);
    printf("  RFC 6811 三态验证:      %d checks\n", n);
    n = 0; test_origin(&n);
    printf("  Route Origin ASN:       %d checks\n", n);
    n = 0; test_oid(&n);
    printf("  OID 的 DER 编码:        %d checks\n", n);

    printf("rpki_demo: %d checks passed\n", g_checks);
    return 0;
}
