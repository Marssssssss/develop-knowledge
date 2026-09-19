/*
 * vfs_statx.c — statx(2) 掩码语义与 st_mode 字段映射
 *
 * 编译: gcc -O2 -Wall -Wextra vfs_statx.c -o vfs_statx
 * 运行: ./vfs_statx
 *
 * 演示:
 *   1. STATX_BASIC_STATS / STATX_ALL 的位构成（STATX_ALL 已废弃）
 *   2. 请求掩码 -> 返回掩码：不支持的位被清、顺手可得的位白送
 *   3. stx_attributes 必须与 stx_attributes_mask 相与
 *   4. st_mode 的 S_IFMT 类型判定与 stx_blocks 的 512B 单位
 *   5. AT_STATX_* 同步三态的合法性校验
 *
 * 常量取自 include/uapi/linux/stat.h 与 fcntl.h。
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <sys/stat.h>

/* 老 glibc 可能没有这些定义，本 demo 自带一份以便独立编译 */
#ifndef STATX_TYPE
#define STATX_TYPE          0x00000001U
#define STATX_MODE          0x00000002U
#define STATX_NLINK         0x00000004U
#define STATX_UID           0x00000008U
#define STATX_GID           0x00000010U
#define STATX_ATIME         0x00000020U
#define STATX_MTIME         0x00000040U
#define STATX_CTIME         0x00000080U
#define STATX_INO           0x00000100U
#define STATX_SIZE          0x00000200U
#define STATX_BLOCKS        0x00000400U
#define STATX_BASIC_STATS   0x000007ffU
#define STATX_BTIME         0x00000800U
#define STATX_ALL           0x00000fffU  /* 已废弃 */
#define STATX_MNT_ID        0x00001000U
#define STATX_DIOALIGN      0x00002000U
#define STATX_MNT_ID_UNIQUE 0x00004000U
#define STATX__RESERVED     0x80000000U
#endif

#ifndef STATX_ATTR_COMPRESSED
#define STATX_ATTR_COMPRESSED 0x00000004ULL
#define STATX_ATTR_IMMUTABLE  0x00000010ULL
#define STATX_ATTR_APPEND     0x00000020ULL
#define STATX_ATTR_NODUMP     0x00000040ULL
#define STATX_ATTR_ENCRYPTED  0x00000800ULL
#define STATX_ATTR_VERITY     0x00100000ULL
#define STATX_ATTR_DAX        0x00200000ULL
#endif

#ifndef AT_STATX_SYNC_TYPE
#define AT_STATX_SYNC_TYPE   0x6000
#define AT_STATX_SYNC_AS_STAT 0x0000
#define AT_STATX_FORCE_SYNC  0x2000
#define AT_STATX_DONT_SYNC   0x4000
#endif

/* 一个文件系统的 statx 能力 */
typedef struct {
    const char *name;
    uint32_t    supported;   /* 能填出来的位 */
    uint32_t    free_bits;   /* 没请求也顺手填的位 */
    uint64_t    attr_mask;   /* stx_attributes_mask */
    uint64_t    attrs;       /* stx_attributes 原始值 */
} fs_cap;

static const uint32_t all_bits[] = {
    STATX_TYPE, STATX_MODE, STATX_NLINK, STATX_UID, STATX_GID,
    STATX_ATIME, STATX_MTIME, STATX_CTIME, STATX_INO, STATX_SIZE,
    STATX_BLOCKS, STATX_BTIME, STATX_MNT_ID, STATX_DIOALIGN,
    STATX_MNT_ID_UNIQUE
};
#define ALL_BITS_N (sizeof(all_bits) / sizeof(all_bits[0]))

/* 按 stat.h 头注释的四条规则算出 stx_mask；返回 -1 表示 EINVAL */
static int resolve_mask(const fs_cap *f, uint32_t req, uint32_t *out)
{
    if (req & STATX__RESERVED)
        return -1;
    uint32_t got = 0;
    for (size_t i = 0; i < ALL_BITS_N; i++) {
        uint32_t b = all_bits[i];
        if (req & b) {
            if (f->supported & b)
                got |= b;      /* 显式请求且支持 */
            continue;          /* 不支持：清位，可能编造兼容值 */
        }
        if ((f->free_bits & b) && (f->supported & b))
            got |= b;          /* 没请求但顺手可得 */
    }
    *out = got;
    return 0;
}

static const char *sync_mode(unsigned flags)
{
    switch (flags & AT_STATX_SYNC_TYPE) {
    case AT_STATX_FORCE_SYNC: return "force_sync";
    case AT_STATX_DONT_SYNC:  return "dont_sync";
    case AT_STATX_SYNC_AS_STAT: return "as_stat";
    default: return "EINVAL";
    }
}

static void put_mask(uint32_t m)
{
    static const struct { uint32_t b; const char *n; } t[] = {
        { STATX_TYPE, "TYPE" },   { STATX_MODE, "MODE" },
        { STATX_NLINK, "NLINK" }, { STATX_UID, "UID" },
        { STATX_GID, "GID" },     { STATX_INO, "INO" },
        { STATX_SIZE, "SIZE" },   { STATX_BLOCKS, "BLOCKS" },
        { STATX_BTIME, "BTIME" }, { STATX_MNT_ID, "MNT_ID" },
        { STATX_DIOALIGN, "DIOALIGN" },
    };
    int first = 1;
    printf("[");
    for (size_t i = 0; i < sizeof(t) / sizeof(t[0]); i++)
        if (m & t[i].b) {
            printf("%s%s", first ? "" : ",", t[i].n);
            first = 0;
        }
    printf("]");
}

static const char *file_type(mode_t mode)
{
    switch (mode & S_IFMT) {
    case S_IFREG: return "regular";
    case S_IFDIR: return "directory";
    case S_IFLNK: return "symlink";
    case S_IFIFO: return "fifo";
    default:      return "other";
    }
}

int main(void)
{
    const uint32_t basic = STATX_TYPE | STATX_MODE | STATX_NLINK |
                           STATX_UID | STATX_GID | STATX_ATIME |
                           STATX_MTIME | STATX_CTIME | STATX_INO |
                           STATX_SIZE | STATX_BLOCKS;

    fs_cap ext4 = {
        .name = "ext4",
        .supported = basic | STATX_BTIME | STATX_MNT_ID | STATX_DIOALIGN |
                     STATX_MNT_ID_UNIQUE,
        .free_bits = STATX_INO,
        .attr_mask = STATX_ATTR_IMMUTABLE | STATX_ATTR_APPEND |
                     STATX_ATTR_COMPRESSED,
        .attrs = STATX_ATTR_IMMUTABLE | STATX_ATTR_DAX, /* DAX 会被屏蔽 */
    };
    fs_cap nfs = {
        .name = "nfs",
        .supported = basic,
    };

    printf("== 位构成 ==\n");
    printf("  STATX_BASIC_STATS = %#x (11 个基础位之或: %s)\n",
           STATX_BASIC_STATS, basic == STATX_BASIC_STATS ? "一致" : "不一致");
    printf("  STATX_ALL         = %#x == BASIC_STATS|BTIME: %s (已废弃)\n",
           STATX_ALL,
           STATX_ALL == (STATX_BASIC_STATS | STATX_BTIME) ? "是" : "否");

    printf("== 请求 vs 返回 ==\n");
    uint32_t got;
    struct { const fs_cap *f; uint32_t req; const char *tag; } cases[] = {
        { &ext4, STATX_SIZE,  "ext4 请求 SIZE " },
        { &ext4, STATX_BTIME, "ext4 请求 BTIME" },
        { &nfs,  STATX_BTIME, "nfs  请求 BTIME" },
        { &ext4, 0,           "ext4 请求 空   " },
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        int rc = resolve_mask(cases[i].f, cases[i].req, &got);
        printf("  %s -> ", cases[i].tag);
        if (rc < 0) { printf("EINVAL\n"); continue; }
        put_mask(got);
        printf("  (请求 ");
        put_mask(cases[i].req);
        printf(", %s)\n", got == cases[i].req ? "相等" : "不相等");
    }

    printf("== 属性位屏蔽 ==\n");
    printf("  ext4 raw=%#llx mask=%#llx 可用=%#llx (DAX 被屏蔽: %s)\n",
           (unsigned long long)ext4.attrs,
           (unsigned long long)ext4.attr_mask,
           (unsigned long long)(ext4.attrs & ext4.attr_mask),
           ((ext4.attrs & ext4.attr_mask) & STATX_ATTR_DAX) ? "否" : "是");
    printf("  nfs  raw=%#llx mask=%#llx 可用=%#llx\n",
           (unsigned long long)nfs.attrs,
           (unsigned long long)nfs.attr_mask,
           (unsigned long long)(nfs.attrs & nfs.attr_mask));

    printf("== 非法组合 ==\n");
    printf("  mask 含 STATX__RESERVED -> %s\n",
           resolve_mask(&ext4, STATX__RESERVED, &got) < 0 ? "EINVAL" : "ok");
    printf("  FORCE|DONT              -> %s\n",
           sync_mode(AT_STATX_FORCE_SYNC | AT_STATX_DONT_SYNC));
    printf("  默认标志                -> %s\n", sync_mode(0));

    printf("== st_mode / stx_blocks ==\n");
    printf("  0100644 -> %s, 0120777 -> %s\n",
           file_type(0100644), file_type(0120777));
    printf("  8 个 stx_blocks -> %u 字节（不是 blksize 也不是 1024）\n",
           8u * 512u);

    return 0;
}
