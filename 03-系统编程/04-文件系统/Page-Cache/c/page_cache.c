/*
 * page_cache.c — Linux page cache + writeback 演示
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic page_cache.c -o page_cache
 * 运行: ./page_cache
 *
 * 演示:
 *   1. write() 后数据只在 page cache(dirty 状态)— 读 /proc/meminfo 看 Dirty
 *   2. posix_fadvise(POSIX_FADV_DONTNEED) 释放 cache 页
 *   3. sync_file_range(SYNC_FILE_RANGE_WRITE) 触发精细 writeback
 *   4. POSIX_FADV_RANDOM 关预读 vs POSIX_FADV_SEQUENTIAL 翻倍预读
 *
 * 注意:本 demo 在 Linux 上运行(Linux-specific syscall)。
 *      POSIX_FADV_DONTNEED 的 dirty 页不释放(man page 原话),故 demo 中
 *      先 fsync 再 fadvise。
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <errno.h>

#define FILE_MB       16
#define FILE_SIZE     (FILE_MB * 1024 * 1024)
#define HALF_SIZE     (FILE_SIZE / 2)

/* 读 /proc/meminfo 拿 Cached/Dirty 大小(KB) */
static long read_meminfo_kb(const char *key) {
    FILE *f = fopen("/proc/meminfo", "r");
    if (!f) return -1;
    char line[256];
    long val = -1;
    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, key, strlen(key)) == 0) {
            sscanf(line, "%*s %ld", &val);
            break;
        }
    }
    fclose(f);
    return val;
}

static void print_cache_status(const char *label) {
    long cached = read_meminfo_kb("Cached:");
    long dirty  = read_meminfo_kb("Dirty:");
    printf("[%s] Cached=%ld KB, Dirty=%ld KB\n",
           label, cached, dirty);
}

/* === demo 1: write 后 page cache dirty 状态 ============== */
static void demo_write_dirty(void) {
    printf("\n=== demo 1: write() fills page cache with dirty pages ===\n");
    print_cache_status("before write");

    int fd = open("cache_demo.bin", O_RDWR | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) { perror("open"); exit(1); }
    if (ftruncate(fd, FILE_SIZE) < 0) { perror("ftruncate"); exit(1); }

    /* 写 16 MB,page cache 应增加 ~16 MB Dirty */
    char *buf = malloc(FILE_SIZE);
    memset(buf, 0x42, FILE_SIZE);
    ssize_t n = write(fd, buf, FILE_SIZE);
    if (n != FILE_SIZE) { perror("write"); exit(1); }
    printf("wrote %zd MB to fd\n", n / (1024 * 1024));
    free(buf);

    print_cache_status("after write (no sync)");
    /* 期望:Dirt 明显增加 */

    /* 释放 */
    if (fsync(fd) < 0) perror("fsync");
    print_cache_status("after fsync");
    close(fd);
}

/* === demo 2: POSIX_FADV_DONTNEED 释放 cache 页 ============== */
static void demo_fadvise_dontneed(void) {
    printf("\n=== demo 2: posix_fadvise(POSIX_FADV_DONTNEED) releases pages ===\n");
    int fd = open("cache_demo.bin", O_RDONLY);
    if (fd < 0) { perror("open"); exit(1); }

    /* 触发 page cache 把整个文件预读进来 */
    posix_fadvise(fd, 0, 0, POSIX_FADV_WILLNEED);
    print_cache_status("after WILLNEED");

    /* 释放前 8 MB */
    int r = posix_fadvise(fd, 0, HALF_SIZE, POSIX_FADV_DONTNEED);
    if (r != 0) { fprintf(stderr, "fadvise DONTNEED failed: %s\n", strerror(r)); }
    print_cache_status("after DONTNEED 0..8MB");
    /* 期望:Cached 略减(已写盘的页不释放) */

    close(fd);
}

/* === demo 3: sync_file_range 触发精细 writeback ============ */
static void demo_sync_file_range(void) {
    printf("\n=== demo 3: sync_file_range() fine-grained writeback ===\n");
    int fd = open("cache_demo.bin", O_RDWR);
    if (fd < 0) { perror("open"); exit(1); }

    /* 制造一批 dirty 页 */
    char buf[4096] = "XXXX";
    pwrite(fd, buf, sizeof(buf), FILE_SIZE - 4096);
    print_cache_status("after pwrite (dirty)");

    /* sync_file_range:WRITE 触发异步 writeback */
    int r = sync_file_range(fd, 0, FILE_SIZE,
        SYNC_FILE_RANGE_WAIT_BEFORE | SYNC_FILE_RANGE_WRITE |
        SYNC_FILE_RANGE_WAIT_AFTER);
    if (r < 0) { perror("sync_file_range"); }
    print_cache_status("after sync_file_range(WRITE|WAIT)");
    /* 期望:Dirty 下降(writeback 已生效) */

    /* 对比:用 fsync 也刷 */
    fsync(fd);
    print_cache_status("after fsync");
    close(fd);
}

/* === demo 4: RANDOM 关预读 vs SEQUENTIAL 加倍预读 ========= */
static void demo_fadvise_readahead(void) {
    printf("\n=== demo 4: POSIX_FADV_RANDOM vs SEQUENTIAL readahead ===\n");
    int fd = open("cache_demo.bin", O_RDONLY);
    if (fd < 0) { perror("open"); exit(1); }

    /* RANDOM 模式:关闭预读。man page 原文 "POSIX_FADV_RANDOM disables file
     * readahead entirely" */
    posix_fadvise(fd, 0, 0, POSIX_FADV_RANDOM);
    printf("set POSIX_FADV_RANDOM  (readahead OFF)\n");

    /* 注意:实际预读窗口大小不可直接读取;内核内部状态。
     * 但 /sys/block/<dev>/queue/read_ahead_kb 可读 backing device 默认值 */
    FILE *f = fopen("/sys/block/sda/queue/read_ahead_kb", "r");
    int ra_default = -1;
    if (f) { fscanf(f, "%d", &ra_default); fclose(f); }
    printf("backing device default readahead: %d KB (RANDOM → 0, SEQUENTIAL → ×2)\n",
           ra_default);

    /* SEQUENTIAL 模式:readahead 翻倍 */
    posix_fadvise(fd, 0, 0, POSIX_FADV_SEQUENTIAL);
    printf("set POSIX_FADV_SEQUENTIAL  (readahead ×2)\n");

    /* NORMAL 恢复默认 */
    posix_fadvise(fd, 0, 0, POSIX_FADV_NORMAL);
    printf("set POSIX_FADV_NORMAL  (default readahead)\n");

    close(fd);
    printf("(* 注:* 内核实际 readahead window 不可直接读,但 /sys/block/sda/queue/read_ahead_kb\n");
    printf("         显示 backing device 默认值,可作为参照)\n");
}

int main(void) {
    /* POSIX_FADV_* 的常量通常已通过 <fcntl.h> 定义 */
    demo_write_dirty();
    demo_fadvise_dontneed();
    demo_sync_file_range();
    demo_fadvise_readahead();

    unlink("cache_demo.bin");
    printf("\nall 4 demos done\n");
    return 0;
}