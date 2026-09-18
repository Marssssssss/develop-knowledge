/*
 * rpki_impl.h —— ROA / VRP / RFC 6811 源验证的最小实现
 *
 * 作为实现头被 rpki_demo.c 文本级 #include（同一翻译单元，只用 .c 编译）。
 *
 * 与 Python 版的分工：C 版把重点放在「路由器数据面真正要跑的东西」——
 * 前缀的位比较、maxLength 判定、三态输出——以及 ROA 里 BIT STRING 的
 * **左对齐**编码（这是最容易写错、且只在非 8 倍数位宽时才暴露的地方）。
 */
#ifndef RPKI_IMPL_H
#define RPKI_IMPL_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define RPKI_IPV4 1
#define RPKI_IPV6 2

/* RFC 6811 §2 的三个验证状态 */
typedef enum {
    RPKI_NOT_FOUND = 0,
    RPKI_VALID = 1,
    RPKI_INVALID = 2
} rpki_state;

static const char *rpki_state_name(rpki_state s)
{
    return s == RPKI_VALID ? "Valid" : (s == RPKI_INVALID ? "Invalid" : "NotFound");
}

typedef struct {
    uint8_t afi;        /* RPKI_IPV4 / RPKI_IPV6 */
    uint8_t length;     /* 前缀长度（位） */
    uint8_t addr[16];   /* 网络字节序，完整位宽（IPv4 只用前 4 字节） */
} rpki_prefix;

typedef struct {
    rpki_prefix prefix;
    uint8_t max_length; /* 展开自 ROA 时已把「缺省」填成 prefix.length */
    uint32_t asn;
} rpki_vrp;

static int rpki_prefix_bits(uint8_t afi)
{
    return afi == RPKI_IPV4 ? 32 : 128;
}

static void rpki_prefix_make(rpki_prefix *p, uint8_t afi, const uint8_t *addr,
                             uint8_t length)
{
    memset(p->addr, 0, sizeof(p->addr));
    p->afi = afi;
    p->length = length;
    if (addr != NULL) {
        memcpy(p->addr, addr, (size_t)(rpki_prefix_bits(afi) / 8));
    }
}

/* 解析 a.b.c.d/n（C 版只做 IPv4 文本解析；IPv6 用 rpki_prefix_make 构造） */
static int rpki_prefix_parse4(rpki_prefix *p, const char *text)
{
    unsigned octet[4], idx = 0, value = 0, digits = 0, length = 32;
    const char *s = text;
    for (; *s != '\0'; s++) {
        if (*s >= '0' && *s <= '9') {
            value = value * 10 + (unsigned)(*s - '0');
            digits++;
            if (value > 255 || digits > 3) return -1;
        } else if (*s == '.') {
            if (digits == 0 || idx >= 3) return -1;
            octet[idx++] = value;
            value = 0;
            digits = 0;
        } else if (*s == '/') {
            if (digits == 0 || idx != 3) return -1;
            octet[idx++] = value;
            if (s[1] == '\0') return -1;
            length = 0;
            for (s++; *s != '\0'; s++) {
                if (*s < '0' || *s > '9') return -1;
                length = length * 10 + (unsigned)(*s - '0');
            }
            if (length > 32) return -1;
            rpki_prefix_make(p, RPKI_IPV4, NULL, (uint8_t)length);
            memcpy(p->addr, octet, 4);
            return 0;
        } else {
            return -1;
        }
    }
    if (digits == 0 || idx != 3) return -1;
    octet[idx] = value;
    rpki_prefix_make(p, RPKI_IPV4, NULL, 32);
    memcpy(p->addr, octet, 4);
    return 0;
}

/* RFC 6811 §2 的 Covered：被比较的前缀不更长，且指定的所有位相同 */
static int rpki_prefix_contains(const rpki_prefix *sup, const rpki_prefix *sub)
{
    unsigned full_bytes, rest_bits, i;
    if (sup->afi != sub->afi || sup->length > sub->length) {
        return 0;
    }
    full_bytes = sup->length / 8;
    rest_bits = sup->length % 8;
    for (i = 0; i < full_bytes; i++) {
        if (sup->addr[i] != sub->addr[i]) return 0;
    }
    if (rest_bits > 0) {
        uint8_t mask = (uint8_t)(0xFF << (8 - rest_bits));
        if ((sup->addr[full_bytes] & mask) != (sub->addr[full_bytes] & mask)) {
            return 0;
        }
    }
    return 1;
}

/*
 * RFC 6811 §2.1 的验证过程：遍历所有 Covered 的 VRP，
 * 只有「路由前缀长度 ≤ VRP 最大长度」且「源 AS 相等且双方都不为 0」才算 Matched。
 * origin_asn 用 asn == 0 表示规范里的 NONE（AS 0 也是保留值，两者都不会 Matched）。
 */
static rpki_state rpki_validate(const rpki_vrp *vrps, size_t count,
                                const rpki_prefix *route_prefix,
                                uint32_t origin_asn)
{
    int covered = 0;
    size_t i;
    for (i = 0; i < count; i++) {
        if (!rpki_prefix_contains(&vrps[i].prefix, route_prefix)) {
            continue;
        }
        covered = 1;
        if (route_prefix->length <= vrps[i].max_length && origin_asn != 0 &&
            vrps[i].asn != 0 && origin_asn == vrps[i].asn) {
            return RPKI_VALID;
        }
    }
    return covered ? RPKI_INVALID : RPKI_NOT_FOUND;
}

/* 合法的 ROA 参数检查（RFC 6482 §3.3）：缺省 maxLength 只授权精确前缀 */
static int rpki_max_length_ok(const rpki_prefix *p, int has_max, uint8_t max_length)
{
    if (!has_max) {
        return 1;
    }
    return max_length >= p->length && max_length <= rpki_prefix_bits(p->afi);
}

/*
 * ROA 里 IPAddress 是 BIT STRING：前缀位**左对齐**，低位补零到整字节，
 * 未使用位数 = 8*字节数 - 前缀长度。返回内容长度（含 1 字节未使用位数）。
 */
static size_t rpki_prefix_bitstring(const rpki_prefix *p, uint8_t *out)
{
    size_t nbytes = (size_t)((p->length + 7) / 8), i;
    uint8_t mask;
    uint8_t buf[16];
    memset(buf, 0, sizeof(buf));
    memcpy(buf, p->addr, sizeof(buf));
    if (p->length % 8 != 0) {
        mask = (uint8_t)(0xFF << (8 - p->length % 8));
        buf[p->length / 8] &= mask;
    }
    out[0] = (uint8_t)(8 * nbytes - p->length);
    for (i = 0; i < nbytes; i++) {
        out[1 + i] = buf[i];
    }
    return nbytes + 1;
}

/*
 * OID 的 DER 编码：前两个分量合并成 40*a+b，各分量 base-128（最高位是续位标志）。
 * 用来核对 ROA 的内容类型 OID（RFC 6482 §4 的 id-ct-routeOriginAuthz）。
 */
static size_t rpki_oid_encode(const unsigned *arcs, size_t count, uint8_t *out)
{
    size_t i, n = 0;
    if (count < 2 || arcs[0] > 2) {
        return 0;
    }
    for (i = 0; i < count; i++) {
        uint8_t tmp[10];
        size_t len = 0;
        uint64_t value;
        if (i == 1) {
            continue;   /* 第二个分量已与第一个合并 */
        }
        value = (i == 0) ? (uint64_t)(arcs[0] * 40 + arcs[1]) : (uint64_t)arcs[i];
        tmp[len++] = (uint8_t)(value & 0x7F);
        value >>= 7;
        while (value) {
            tmp[len++] = (uint8_t)(0x80 | (value & 0x7F));
            value >>= 7;
        }
        while (len > 0) {
            out[n++] = tmp[--len];
        }
    }
    return n;
}

/* --------------------------------------------------------- 源 AS 推导 */
#define RPKI_AS_SET 1
#define RPKI_AS_SEQUENCE 2
#define RPKI_AS_CONFED_SEQUENCE 3
#define RPKI_AS_CONFED_SET 4

/*
 * RFC 6811 §2：Route Origin ASN 由**最后一个 AS_PATH 段的类型**决定，
 * 而不是「最后一个出现的 AS 号」。返回 0 表示规范里的 NONE。
 */
static uint32_t rpki_origin_asn(const uint8_t *seg_types, const uint32_t *seg_last,
                                size_t seg_count, uint32_t local_asn)
{
    uint8_t last;
    if (seg_count == 0) {
        return local_asn;
    }
    last = seg_types[seg_count - 1];
    if (last == RPKI_AS_CONFED_SEQUENCE || last == RPKI_AS_CONFED_SET) {
        return local_asn;
    }
    if (last != RPKI_AS_SEQUENCE) {
        return 0;
    }
    return seg_last[seg_count - 1];
}

#endif /* RPKI_IMPL_H */
