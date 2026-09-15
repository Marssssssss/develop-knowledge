/* user namespace 的 uid_map/gid_map 解析、校验与双向翻译(C 实现)。
 *
 * 规则依据 man7.org 的 user_namespaces(7) 与 newuidmap(1):
 *   https://man7.org/linux/man-pages/man7/user_namespaces.7.html
 *   https://man7.org/linux/man-pages/man1/newuidmap.1.html
 *
 * 本文件实现:
 *   - 解析 "ID-inside-ns ID-outside-ns size" 行(至多 MAX_RANGES 行)
 *   - 写入合法性校验:只能写一次、须换行结尾、须偏移 0、<= 一页、至少一行、
 *     行数上限、区间不得重叠、无特权只能单行且必须映射自身 euid
 *   - 双向翻译(inside<->outside)与未映射 ID 的 overflow 语义
 *
 * 编译:gcc -O2 -Wall -Wextra -pedantic -std=c11 uid_map.c -o uid_map
 * 注意:本机无 C 工具链,本文件走人工代码审查 + 括号配平程序化校验。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PAGE_SIZE       4096u
#define MAX_RANGES      340u          /* Linux 4.16+ 的行数上限 */
#define LEGACY_RANGES   5u            /* Linux 4.14 之前 */
#define OVERFLOW_ID     65534u        /* /proc/sys/kernel/overflowuid 默认值 */
#define NO_ID           4294967295u   /* (uid_t)-1:uid_map 中"未映射"的表示 */
#define MAX_NESTING     32u           /* Linux 3.11+ 的嵌套层数上限 */

/* 一行映射:inside 与 outside 各是一段长度为 size 的连续区间。 */
typedef struct {
    unsigned inside_start;
    unsigned outside_start;
    unsigned size;
} Range;

typedef struct {
    char     name[32];
    Range    ranges[MAX_RANGES];
    unsigned count;
    int      written;                 /* 一个 map 只能写一次 */
    int      is_gid;
    int      setgroups_denied;        /* 写 gid_map 前必须先 deny */
} UidMap;

/* 错误码:与内核 errno 名字对应,便于和 Python/Go 实现对齐。 */
typedef enum {
    MAP_OK = 0,
    MAP_EPERM,
    MAP_EINVAL,
    MAP_EUSERS
} MapStatus;

static const char *status_name(MapStatus s)
{
    switch (s) {
    case MAP_OK:     return "OK";
    case MAP_EPERM:  return "EPERM";
    case MAP_EINVAL: return "EINVAL";
    default:         return "EUSERS";
    }
}

/* 解析 uid_map 文本。返回 MAP_OK 或 MAP_EINVAL。
 * 规则:至少一行、必须是三个数字、行尾必须有 '\n'、总字节 < 一页、行数不超上限。 */
static MapStatus parse_map(const char *text, unsigned kernel_modern,
                           Range *out, unsigned *out_count)
{
    unsigned n = 0;
    const char *p = text;

    if (text == NULL || *text == '\0' || strchr(text, '\n') == NULL) {
        return MAP_EINVAL;                    /* 空 或 不以换行结尾 */
    }
    if (strlen(text) >= PAGE_SIZE) {
        return MAP_EINVAL;                    /* 写入总字节必须小于一页 */
    }
    while (*p != '\0') {
        unsigned a, b, c;
        if (sscanf(p, "%u %u %u", &a, &b, &c) != 3) {
            return MAP_EINVAL;
        }
        if (n >= MAX_RANGES) {
            return MAP_EINVAL;
        }
        out[n].inside_start  = a;
        out[n].outside_start = b;
        out[n].size          = c;
        n++;
        const char *nl = strchr(p, '\n');
        if (nl == NULL) {
            return MAP_EINVAL;                /* 最后一行也必须有换行 */
        }
        p = nl + 1;
    }
    if (n == 0) {
        return MAP_EINVAL;                    /* 至少要写一行 */
    }
    if (n > (kernel_modern ? MAX_RANGES : LEGACY_RANGES)) {
        return MAP_EINVAL;                    /* 4.14 之前上限为 5 行 */
    }
    *out_count = n;
    return MAP_OK;
}

/* 区间重叠检测:inside 空间与 outside 空间都必须互不重叠(O(n^2),n<=340 足够)。 */
static MapStatus check_overlap(const Range *r, unsigned n)
{
    unsigned i, j;
    for (i = 0; i < n; i++) {
        for (j = i + 1; j < n; j++) {
            if (r[i].inside_start < r[j].inside_start + r[j].size &&
                r[j].inside_start < r[i].inside_start + r[i].size) {
                return MAP_EINVAL;
            }
            if (r[i].outside_start < r[j].outside_start + r[j].size &&
                r[j].outside_start < r[i].outside_start + r[i].size) {
                return MAP_EINVAL;
            }
        }
    }
    return MAP_OK;
}

/* 完整的写入校验。writer_caps:写入者在父 ns 是否具备 CAP_SETUID/SETGID。 */
static MapStatus write_map(UidMap *m, const char *text,
                           int writer_caps, unsigned writer_euid,
                           unsigned creator_euid, unsigned offset)
{
    Range    tmp[MAX_RANGES];
    unsigned n = 0;
    MapStatus st;

    if (m->written) {
        return MAP_EPERM;                     /* 只能写一次 */
    }
    if (offset != 0) {
        return MAP_EINVAL;                    /* 必须从偏移 0 写 */
    }
    st = parse_map(text, 1, tmp, &n);
    if (st != MAP_OK) {
        return st;
    }
    st = check_overlap(tmp, n);
    if (st != MAP_OK) {
        return st;
    }
    if (!writer_caps) {
        /* 无特权:只能一行、长度必须为 1、必须映射自身有效 UID */
        if (n != 1 || tmp[0].size != 1) {
            return MAP_EPERM;
        }
        if (tmp[0].outside_start != writer_euid || writer_euid != creator_euid) {
            return MAP_EPERM;
        }
        if (m->is_gid && !m->setgroups_denied) {
            return MAP_EPERM;                 /* 写 gid_map 前须 deny setgroups */
        }
    }
    memcpy(m->ranges, tmp, n * sizeof(Range));
    m->count   = n;
    m->written = 1;
    return MAP_OK;
}

/* inside -> outside;未映射返回 overflow 65534。 */
static unsigned to_outside(const UidMap *m, unsigned id)
{
    unsigned i;
    for (i = 0; i < m->count; i++) {
        const Range *r = &m->ranges[i];
        if (id >= r->inside_start && id < r->inside_start + r->size) {
            return r->outside_start + (id - r->inside_start);
        }
    }
    return OVERFLOW_ID;
}

/* outside -> inside;未映射返回 overflow 65534。 */
static unsigned from_outside(const UidMap *m, unsigned id)
{
    unsigned i;
    for (i = 0; i < m->count; i++) {
        const Range *r = &m->ranges[i];
        if (id >= r->outside_start && id < r->outside_start + r->size) {
            return r->inside_start + (id - r->outside_start);
        }
    }
    return OVERFLOW_ID;
}

/* 读 uid_map 第二字段的例外:未映射显示 4294967295 而非 65534。 */
static unsigned second_field_or_no_id(const UidMap *m, unsigned id)
{
    unsigned v = to_outside(m, id);
    return (v == OVERFLOW_ID) ? NO_ID : v;
}

static void dump(const char *label, unsigned got, unsigned want)
{
    printf("  %-52s got=%-10u want=%-10u %s\n", label, got, want,
           (got == want) ? "OK" : "MISMATCH");
}

static void dump_status(const char *label, MapStatus got, MapStatus want)
{
    printf("  %-52s got=%-8s want=%-8s %s\n", label, status_name(got),
           status_name(want), (got == want) ? "OK" : "MISMATCH");
}

int main(void)
{
    /* 官方文档里的真实数字:testuser:231072:65536 */
    const unsigned SUB_START = 231072u, SUB_COUNT = 65536u;
    char text[64];
    UidMap ns;

    printf("== uid_map 模型(C) ==\n");

    memset(&ns, 0, sizeof(ns));
    snprintf(ns.name, sizeof(ns.name), "ns1");
    snprintf(text, sizeof(text), "0 %u %u\n", SUB_START, SUB_COUNT);
    dump_status("写入单项映射", write_map(&ns, text, 1, 1000, 1000, 0), MAP_OK);
    dump("容器 uid 0 -> 宿主", to_outside(&ns, 0u), SUB_START);
    dump("区间末位 65535 -> 宿主", to_outside(&ns, 65535u), SUB_START + SUB_COUNT - 1u);
    dump("越界 65536 -> overflow", to_outside(&ns, 65536u), OVERFLOW_ID);
    dump("宿主 231072 -> 容器", from_outside(&ns, 231072u), 0u);
    dump("宿主 231071(未映射)", from_outside(&ns, 231071u), OVERFLOW_ID);

    dump_status("二次写入 -> EPERM",
                write_map(&ns, "0 0 1\n", 1, 1000, 1000, 0), MAP_EPERM);

    /* 无特权:只能单行且映射自身 */
    {
        UidMap u;
        memset(&u, 0, sizeof(u));
        dump_status("无特权写两行 -> EPERM",
                    write_map(&u, "0 1000 1\n1 2000 1\n", 0, 1000, 1000, 0), MAP_EPERM);
        dump_status("无特权映射他人 UID -> EPERM",
                    write_map(&u, "0 2000 1\n", 0, 1000, 1000, 0), MAP_EPERM);
        dump_status("无特权映射自身 -> OK",
                    write_map(&u, "0 1000 1\n", 0, 1000, 1000, 0), MAP_OK);
        dump("userns-remap:inside 0 -> 父 ns 1000", to_outside(&u, 0u), 1000u);
    }

    /* gid_map 必须先 deny setgroups */
    {
        UidMap g;
        memset(&g, 0, sizeof(g));
        g.is_gid = 1;
        dump_status("gid_map 未 deny -> EPERM",
                    write_map(&g, "0 1000 1\n", 0, 1000, 1000, 0), MAP_EPERM);
        g.setgroups_denied = 1;
        dump_status("gid_map deny 后 -> OK",
                    write_map(&g, "0 1000 1\n", 0, 1000, 1000, 0), MAP_OK);
    }

    /* 格式与重叠 */
    {
        UidMap t;
        memset(&t, 0, sizeof(t));
        dump_status("不以换行结尾 -> EINVAL",
                    write_map(&t, "0 1000 1", 1, 1000, 1000, 0), MAP_EINVAL);
        memset(&t, 0, sizeof(t));
        dump_status("非零偏移 -> EINVAL",
                    write_map(&t, "0 1000 1\n", 1, 1000, 1000, 8u), MAP_EINVAL);
        memset(&t, 0, sizeof(t));
        dump_status("区间重叠 -> EINVAL",
                    write_map(&t, "0 1000 100\n50 2000 100\n", 1, 1000, 1000, 0), MAP_EINVAL);
    }

    /* 初始命名空间的伪映射:4294967295 特意不映射 */
    {
        UidMap init;
        memset(&init, 0, sizeof(init));
        snprintf(init.name, sizeof(init.name), "init");
        init.ranges[0].inside_start = 0u;
        init.ranges[0].outside_start = 0u;
        init.ranges[0].size = NO_ID;
        init.count = 1u;
        init.written = 1;
        dump("读 uid_map 未映射第二字段显示 -1",
             second_field_or_no_id(&init, NO_ID), NO_ID);
        dump("4294967294 正常映射", second_field_or_no_id(&init, NO_ID - 1u), NO_ID - 1u);
    }

    printf("  嵌套层数上限 %u 层,超出 -> %s\n", MAX_NESTING,
           status_name(MAP_EUSERS));
    return 0;
}
