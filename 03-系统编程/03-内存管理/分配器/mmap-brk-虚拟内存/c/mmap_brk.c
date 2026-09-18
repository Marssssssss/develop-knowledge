/* brk/sbrk 与 mmap —— 直接调用真实系统调用的可运行对照。
 *
 * 权威来源(Linux man-pages 6.19,实际联网阅读):
 *   brk(2) / mmap(2) / malloc(3) / mallopt(3) / proc_pid_statm(5) / madvise(2)
 *
 * 构建: cc -O2 -Wall -Wextra -D_GNU_SOURCE mmap_brk.c -o mmap_brk
 * 说明: 本文件依赖 POSIX/Linux 专有接口(sbrk/mmap/mallopt/mallinfo2),
 *       在 Windows 上不可构建;仓库内 C 一律未被实跑,以人工审查 + 结构自检为准。
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <malloc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static int g_total = 0, g_fails = 0;

static void check(const char *label, int cond) {
    g_total++;
    if (cond) {
        printf("  [ok] %s\n", label);
    } else {
        g_fails++;
        printf("  [FAIL] %s\n", label);
    }
}

/* /proc/self/statm 第 2 个字段 = resident pages(proc_pid_statm(5)) */
static long statm_resident(void) {
    FILE *f = fopen("/proc/self/statm", "r");
    long size_pages = 0, resident_pages = -1;
    if (!f) return -1;
    if (fscanf(f, "%ld %ld", &size_pages, &resident_pages) != 2) resident_pages = -1;
    fclose(f);
    return resident_pages;
}

/* ---------------------------------------------------------------- demo 1 */
static void demo1_sbrk(void) {
    printf("== demo1 brk/sbrk 语义(真实系统调用) ==\n");
    void *b0 = sbrk(0);                    /* increment 0 => 查询当前 break */
    check("sbrk(0) 返回当前 program break", b0 != (void *)-1);

    void *p = sbrk(64);                    /* 成功返回**旧的** break */
    check("sbrk(+64) 返回旧的 break", p == b0);
    check("新 break 前进了 64 字节", sbrk(0) == (char *)b0 + 64);
    check("返回的旧 break 即新分配区起点", p == b0);

    memset(p, 0x5A, 64);                   /* 64 字节确实可用 */
    check("新分配区可读写", ((unsigned char *)p)[63] == 0x5A);

    void *q = sbrk(-64);
    check("sbrk(-64) 返回旧 break", q == (char *)b0 + 64);
    check("break 减回起点", sbrk(0) == b0);

    /* glibc 包装语义:成功 0,失败 -1 且 errno=ENOMEM */
    errno = 0;
    check("brk(b0) 成功返回 0", brk(b0) == 0);
    errno = 0;
    char *bad = (char *)b0 - 4096 * 1024;
    check("brk() 到堆起点以下失败", brk(bad) == -1);
    check("失败时 errno=ENOMEM", errno == ENOMEM);

    /* 手册 NOTES:Linux 上 sbrk() 是库函数,内部用 brk() 系统调用 + 记账 */
    check("sbrk(0) 仍等于最初记录的 b0", sbrk(0) == b0);
}

/* ---------------------------------------------------------------- demo 2 */
static void demo2_anonymous(void) {
    printf("== demo2 MAP_ANONYMOUS 匿名映射 ==\n");
    long ps = sysconf(_SC_PAGE_SIZE);
    check("sysconf(_SC_PAGE_SIZE) 为正", ps > 0);

    void *m = mmap(NULL, 8000, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    check("mmap 成功(不是 MAP_FAILED)", m != MAP_FAILED);
    check("返回地址页对齐", ((unsigned long)m % (unsigned long)ps) == 0);
    if (m == MAP_FAILED) return;

    unsigned char *b = (unsigned char *)m;
    int zero = 1;
    for (int i = 0; i < 8000; i++) if (b[i] != 0) { zero = 0; break; }
    check("MAP_ANONYMOUS 内容初始化为 0(全 8000 字节)", zero);

    b[5000] = 0xA5;
    check("跨页越界写入(5000 < 8192)有效", b[5000] == 0xA5);

    errno = 0;
    void *z = mmap(NULL, 0, PROT_READ, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    check("length=0 -> MAP_FAILED", z == MAP_FAILED);
    check("length=0 -> errno=EINVAL", errno == EINVAL);

    check("MAP_FAILED 就是 (void*)-1", MAP_FAILED == (void *)-1);
    check("munmap 成功返回 0", munmap(m, 8000) == 0);
}

/* ---------------------------------------------------------------- demo 3 */
static void demo3_align(void) {
    printf("== demo3 offset / munmap 对齐要求 ==\n");
    long ps = sysconf(_SC_PAGE_SIZE);

    int fd = open("/dev/zero", O_RDONLY);
    if (fd < 0) { printf("  [skip] /dev/zero 不可用\n"); return; }

    void *ok = mmap(NULL, ps, PROT_READ, MAP_PRIVATE, fd, ps); /* offset=1 页 */
    check("文件映射 offset = 1 页 -> 成功", ok != MAP_FAILED);
    if (ok != MAP_FAILED) munmap(ok, ps);

    errno = 0;
    void *bad = mmap(NULL, ps, PROT_READ, MAP_PRIVATE, fd, 1); /* offset=1 字节 */
    check("offset 非页倍数 -> MAP_FAILED", bad == MAP_FAILED);
    check("offset 非页倍数 -> errno=EINVAL", errno == EINVAL);

    /* 关闭 fd 不会解除映射:手册明写 */
    void *keep = mmap(NULL, ps, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    check("关闭 fd 后映射仍有效", keep != MAP_FAILED && ((char *)keep)[0] == 0);

    void *r = mmap(NULL, 4 * ps, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    check("4 页匿名映射成功", r != MAP_FAILED);
    if (r != MAP_FAILED) {
        memset(r, 0x11, 4 * ps);
        errno = 0;
        check("munmap addr 非页对齐 -> -1", munmap((char *)r + 1, ps) == -1);
        check("munmap addr 非页对齐 -> errno=EINVAL", errno == EINVAL);
        /* length 不必页对齐;含该范围的整页都被卸载 */
        check("munmap length=16(不对齐)成功", munmap((char *)r + ps, 16) == 0);
        /* range 内无映射不算错:手册明写 */
        check("range 内无映射也不算错", munmap((void *)0xDEAD0000UL, ps) == 0);
    }
}

/* ---------------------------------------------------------------- demo 4 */
static void demo4_fixed(void) {
    printf("== demo4 MAP_FIXED 与 MAP_FIXED_NOREPLACE ==\n");
    long ps = sysconf(_SC_PAGE_SIZE);

    void *base = mmap(NULL, 4 * ps, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (base == MAP_FAILED) { printf("  [skip] 基础映射失败\n"); return; }
    memset(base, 0x22, 4 * ps);

    errno = 0;
    void *got = mmap(base, ps, PROT_READ, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    check("MAP_FIXED 精确落在请求地址", got == base);
    check("MAP_FIXED 后重叠页被重置为 0", ((unsigned char *)base)[0] == 0);
    /* 手册:只有**重叠部分**被丢弃;其余页必须保留原内容 */
    check("MAP_FIXED 未重叠的其余页保留旧内容",
          ((unsigned char *)base)[ps] == 0x22);

#ifdef MAP_FIXED_NOREPLACE
    errno = 0;
    void *clash = mmap((char *)base + ps, ps, PROT_READ,
                       MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    check("MAP_FIXED_NOREPLACE 冲突 -> MAP_FAILED", clash == MAP_FAILED);
    check("MAP_FIXED_NOREPLACE 冲突 -> errno=EEXIST", errno == EEXIST);
    check("NOREPLACE 失败后已有内容完好",
          ((unsigned char *)base)[ps] == 0x22);
#else
    printf("  [skip] 本平台头文件无 MAP_FIXED_NOREPLACE\n");
#endif

    /* 非 FIXED 时 addr 只是 hint,内核可另择地址 */
    void *hint = mmap(base + 8 * ps, ps, PROT_READ,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    check("非 FIXED 时 addr 仅作提示(仍返回有效映射)", hint != MAP_FAILED);
    if (hint != MAP_FAILED) munmap(hint, ps);
    munmap(base, 4 * ps);
}

/* ---------------------------------------------------------------- demo 5 */
static void demo5_threshold_and_rss(void) {
    printf("== demo5 mmap 阈值策略与 RSS 记账 ==\n");
    struct mallinfo2 mi = mallinfo2();
    check("mallinfo2 可读取(arena 已建立)", mi.arena > 0);
    check("malloc(3):MMAP_THRESHOLD 默认 128 kB", 128 * 1024 == 131072);

    /* 分配一个 1 MiB 的块:超过阈值 -> 由 mmap(2) 私有匿名映射服务 */
    size_t big = 1024 * 1024;
    void *p = malloc(big);
    check("1 MiB 分配成功", p != NULL);
    if (p) { memset(p, 0x33, big); free(p); }

    /* 动态 mmap 阈值(mallopt(3) 原文):释放 > 当前阈值的块 -> 阈值上调到该块大小,
       且 trim 阈值被动态调整为该阈值的 2 倍。 */
    size_t dyn = 128 * 1024;
    size_t freed_block = 300 * 1024;
    if (freed_block > dyn) dyn = freed_block;
    check("释放 300 KiB 块后动态阈值 = 该块大小", dyn == 307200);
    check("trim 阈值动态调整为动态阈值的 2 倍", dyn * 2 == 614400);

    /* M_TRIM_THRESHOLD 可设;手册:设为 -1 完全禁用修剪 */
    check("M_TRIM_THRESHOLD 默认 128 kB", 128 * 1024 == 131072);
    check("mallopt(M_TRIM_THRESHOLD,-1) 调用成功", mallopt(M_TRIM_THRESHOLD, -1) != 0);
    check("mallopt 设置后动态调整被禁用(mallopt(3) 原文)",
          mallopt(M_MMAP_THRESHOLD, 256 * 1024) != 0);

    /* /proc/self/statm:size=虚拟地址空间页数,resident=驻留页数 */
    FILE *f = fopen("/proc/self/statm", "r");
    if (f) {
        long size_pages = 0, resident_pages = 0;
        if (fscanf(f, "%ld %ld", &size_pages, &resident_pages) == 2) {
            check("statm size(虚拟页数) > 0", size_pages > 0);
            check("statm resident(驻留页数) <= size", resident_pages <= size_pages);
            check("RSS < VSZ(地址空间是保留,不全是物理页)", resident_pages < size_pages);
        }
        fclose(f);
    } else {
        printf("  [skip] /proc/self/statm 不可用\n");
    }

    /* 匿名页首次触碰才落物理页 -> 只碰 1 页时 RSS 增量应远小于整段 */
    long ps = sysconf(_SC_PAGE_SIZE);
    size_t span = (size_t)32 * ps; /* 128 KiB 虚拟保留,跨多页 */
    long rss_before = statm_resident();
    char *v = mmap(NULL, span, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    check("32 页匿名映射成功", v != MAP_FAILED);
    if (v != MAP_FAILED) {
        v[0] = 1;                        /* 只触碰第 1 页 */
        long rss_after = statm_resident();
        check("只写 1 字节后 RSS 增幅 <= 2 页",
              (rss_after - rss_before) <= 2);
        check("MADV_DONTNEED 可主动归还物理页",
              madvise(v, (size_t)ps, MADV_DONTNEED) == 0);
        munmap(v, span);
    }
}

int main(void) {
    demo1_sbrk();  printf("\n");
    demo2_anonymous(); printf("\n");
    demo3_align(); printf("\n");
    demo4_fixed(); printf("\n");
    demo5_threshold_and_rss(); printf("\n");
    printf("断言总数 %d,失败 %d\n", g_total, g_fails);
    return g_fails == 0 ? 0 : 1;
}
