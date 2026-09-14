/*
 * page_fault_types.c — 区分次缺页(minor)与主缺页(major),并观察它们的成因(C 版,教学用)
 *
 * 缺页的定义(man7 perf_event_open(2) 对两个软件事件的定义):
 *   PERF_COUNT_SW_PAGE_FAULTS_MIN —— 【不需要】磁盘 I/O 的缺页(零页、COW、page cache 命中、只建页表项)
 *   PERF_COUNT_SW_PAGE_FAULTS_MAJ —— 【需要】磁盘 I/O 的缺页
 * 所以"缺页多" ≠ "内存不够":绝大多数缺页是 minor,反而是好事(说明没打到磁盘)。
 *
 * 本程序用两个权威来源交叉核对计数:
 *   · getrusage(2) 的 ru_minflt / ru_majflt
 *   · /proc/self/stat 的第 10 字段 min_flt、第 12 字段 maj_flt
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic page_fault_types.c -o page_fault_types
 * 运行: ./page_fault_types
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define PAGE 4096
#define PAGES 4096 /* 16 MB */

struct faults {
    long min;
    long maj;
};

/* 来自 /proc/self/stat:comm 里可能有空格与括号,所以从最后一个 ')' 之后开始解析 */
static int read_proc_faults(struct faults *out)
{
    FILE *f = fopen("/proc/self/stat", "r");
    char buf[1024];
    char *p;
    char state;
    long ppid, pgrp, session, tty, tpgid;
    unsigned long flags, minflt, cminflt, majflt, cmajflt;

    if (!f)
        return -1;
    if (!fgets(buf, sizeof(buf), f)) {
        fclose(f);
        return -1;
    }
    fclose(f);
    p = strrchr(buf, ')');
    if (!p)
        return -1;
    /* 字段 3 起:state ppid pgrp session tty_nr tpgid flags minflt cminflt majflt */
    if (sscanf(p + 1, " %c %ld %ld %ld %ld %ld %lu %lu %lu %lu %lu",
               &state, &ppid, &pgrp, &session, &tty, &tpgid, &flags,
               &minflt, &cminflt, &majflt, &cmajflt) != 11)
        return -1;
    out->min = (long)minflt;
    out->maj = (long)majflt;
    return 0;
}

static struct faults sample_faults(void)
{
    struct faults f = {0, 0};
    struct rusage ru;

    if (read_proc_faults(&f) == 0)
        return f;
    if (getrusage(RUSAGE_SELF, &ru) == 0) {
        f.min = ru.ru_minflt;
        f.maj = ru.ru_majflt;
    }
    return f;
}

static struct faults delta(struct faults a, struct faults b)
{
    struct faults d = {b.min - a.min, b.maj - a.maj};
    return d;
}

static void row(const char *what, struct faults d)
{
    printf("  %-42s minor=%6ld  major=%6ld\n", what, d.min, d.maj);
}

static void exp_anon_touch(void)
{
    char *p = mmap(NULL, (size_t)PAGES * PAGE, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    struct faults a, b;

    puts("=== 实验 1:首次触碰匿名内存(零页映射)===");
    a = sample_faults();
    for (int i = 0; i < PAGES; i++)
        p[(size_t)i * PAGE] = 1;
    b = sample_faults();
    row("首次触碰 4096 页(每页写 1 字节)", delta(a, b));
    puts("  -> 全是 minor:内核只需建页表项并把页指向零页/新分配的页,不需磁盘 I/O\n");

    puts("=== 实验 2:madvise(MADV_DONTNEED) 后再触碰 ===");
    a = sample_faults();
    madvise(p, (size_t)PAGES * PAGE, MADV_DONTNEED);
    for (int i = 0; i < PAGES; i++)
        p[(size_t)i * PAGE] = 2;
    b = sample_faults();
    row("丢弃映射后重新触碰 4096 页", delta(a, b));
    puts("  -> 仍然是 minor:匿名页被丢弃后重新分配即可,与磁盘无关");
    puts("     注意:MADV_DONTNEED 会丢数据,常被用作\"强制触发缺页\"的手段\n");

    munmap(p, (size_t)PAGES * PAGE);
}

static void exp_map_populate(void)
{
    struct faults a, b;

    puts("=== 实验 3:MAP_POPULATE 预缺页(把代价提前)===");
    a = sample_faults();
    char *p = mmap(NULL, (size_t)PAGES * PAGE, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
    b = sample_faults();
    row("mmap(..., MAP_POPULATE) 本身", delta(a, b));
    a = sample_faults();
    for (int i = 0; i < PAGES; i++)
        p[(size_t)i * PAGE] = 1;
    b = sample_faults();
    row("之后的首次触碰(已预先建好页表)", delta(a, b));
    puts("  -> 缺页没消失,只是从\"触碰时\"挪到了\"mmap 时\":延迟敏感场景才值得这么换\n");
    munmap(p, (size_t)PAGES * PAGE);
}

static void exp_cow(void)
{
    puts("=== 实验 4:fork 的写时复制(COW)===");
    size_t sz = 1024 * PAGE;
    char *p = mmap(NULL, sz, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    struct faults parent_before, child_before, child_after;
    int pipefd[2];

    for (size_t i = 0; i < sz; i += PAGE)
        p[i] = 7; /* 先让父进程真正拥有这些页 */
    if (pipe(pipefd) != 0)
        return;

    parent_before = sample_faults();
    pid_t pid = fork();
    if (pid == 0) {
        close(pipefd[1]);
        char c;
        if (read(pipefd[0], &c, 1) != 1)
            _exit(2);
        child_before = sample_faults();
        for (size_t i = 0; i < sz; i += PAGE)
            p[i] = 9; /* 子进程写 -> 触发 COW */
        child_after = sample_faults();
        dprintf(STDERR_FILENO, "  %-42s minor=%6ld  major=%6ld\n",
                "子进程写 1024 页(COW 复制)", child_after.min - child_before.min,
                child_after.maj - child_before.maj);
        _exit(0);
    }
    close(pipefd[0]);
    if (write(pipefd[1], "x", 1) != 1)
        perror("write");
    close(pipefd[1]);
    waitpid(pid, NULL, 0);
    struct faults parent_after = sample_faults();
    row("父进程自身(仅 fork,未写)", delta(parent_before, parent_after));
    puts("  -> fork 本身几乎不复制物理页(父进程 major/minor 都很少涨);");
    puts("     真正复制发生在\"某一方写入\"时,表现为【次缺页】(页还在内存里,只做复制)\n");
    munmap(p, sz);
}

static void exp_file_backed(void)
{
    const char *path = "/tmp/pftypes_test.bin";
    int fd;
    char *p;
    struct faults a, b;

    puts("=== 实验 5:文件后备映射 + posix_fadvise(DONTNEED) 驱逐页缓存 ===");
    fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        printf("  跳过:无法创建 %s(%s)\n\n", path, strerror(errno));
        return;
    }
    if (ftruncate(fd, PAGE * 64) != 0)
        perror("ftruncate");
    a = sample_faults();
    p = mmap(NULL, PAGE * 64, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (p == MAP_FAILED) {
        printf("  跳过:mmap 失败(%s)\n\n", strerror(errno));
        close(fd);
        return;
    }
    for (int i = 0; i < 64; i++)
        p[i * PAGE] = 3; /* 写 -> 触发缺页并把页变脏 */
    msync(p, PAGE * 64, MS_SYNC);
    b = sample_faults();
    row("映射 64 页文件并写入", delta(a, b));

    if (posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED) == 0) {
        a = sample_faults();
        volatile char sink = 0;
        for (int i = 0; i < 64; i++)
            sink = (char)(sink + p[i * PAGE]);
        (void)sink;
        b = sample_faults();
        row("fadvise(DONTNEED) 后重新读", delta(a, b));
        puts("  -> 若 major 出现非 0,说明页真的从磁盘回来了;若仍是 minor,");
        puts("     说明页缓存并未被驱逐(该文件系统/权限不允许,或页被判定为仍在使用)");
    } else {
        puts("  posix_fadvise 不可用,跳过驱逐步骤");
    }
    munmap(p, PAGE * 64);
    close(fd);
    unlink(path);
    printf("\n");
}

static void print_field_reference(void)
{
    puts("=== 三个取数口径(互为交叉验证)===");
    puts("  getrusage(2)          ru_minflt / ru_majflt          —— 进程累计,最省事");
    puts("  /proc/self/stat       第 10 字段 min_flt、第 12 字段 maj_flt");
    puts("  perf 软件事件         PAGE_FAULTS / _MIN / _MAJ       —— 可分组、可采样");
    puts("  注意:SMP 下 RSS 相关统计是异步的,值可能不精确(proc.rst 明文);");
    puts("        缺页计数是精确的,所以判断\"是否真打到磁盘\"优先看 major。");
}

int main(void)
{
    puts("缺页(page fault):访问未建映射或页不在内存的地址时的陷阱。");
    puts("  minor = 不需要磁盘 I/O(零页/COW/page cache 命中/只建页表项)");
    puts("  major = 需要磁盘 I/O —— 这才是要关注的\n");

    exp_anon_touch();
    exp_map_populate();
    exp_cow();
    exp_file_backed();
    print_field_reference();
    puts("\n结论:\"缺页多\"不等于\"内存不够\"。排查时先看 major 的绝对值与增速,");
    puts("      再结合 io.pressure / smaps 的 Rss/Pss 定位到底是哪块内存在被反复换入换出。");
    return 0;
}
