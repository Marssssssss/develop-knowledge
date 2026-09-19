/*
 * sparse_holes.c — 真实的 SEEK_HOLE/SEEK_DATA 与 FALLOC_FL_* 演示
 *
 * 编译: gcc -D_GNU_SOURCE -O2 -Wall -Wextra sparse_holes.c -o sparse_holes
 * 运行: ./sparse_holes   （需要 Linux 且文件系统支持打洞，ext4/XFS/Btrfs 均可）
 *
 * 演示:
 *   1. 写两个不相邻的块，中间留一个洞，用 SEEK_DATA/SEEK_HOLE 走一遍
 *   2. 洞中间调用 SEEK_HOLE 会原样返回 offset（lseek(2) 明文规则）
 *   3. 文件末尾是隐式洞；越过 EOF 调用会拿 ENXIO
 *   4. FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE 打洞后 st_blocks 变小而 st_size 不变
 *   5. FALLOC_FL_COLLAPSE_RANGE 的粒度与 EOF 约束（EINVAL）
 *
 * 非 Linux 平台下只打印提示并退出。
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>

#ifdef __linux__
#include <linux/falloc.h>
#endif

#ifndef SEEK_DATA
#define SEEK_DATA 3
#define SEEK_HOLE 4
#endif

#define BLK 4096

static long long xseek(int fd, long long off, int whence, const char *name)
{
    off_t r = lseek(fd, (off_t)off, whence);
    if (r == (off_t)-1) {
        printf("  lseek(%lld, %s) -> 失败 errno=%d (%s)\n",
               off, name, errno, strerror(errno));
        return -1;
    }
    printf("  lseek(%lld, %s) -> %lld\n", off, name, (long long)r);
    return (long long)r;
}

static void show_stat(const char *tag, int fd)
{
    struct stat st;
    if (fstat(fd, &st) == 0) {
        printf("  %-10s st_size=%lld st_blocks=%lld (实际 %lld 字节)\n",
               tag, (long long)st.st_size, (long long)st.st_blocks,
               (long long)st.st_blocks * 512);
    }
}

int main(void)
{
#ifndef __linux__
    printf("本 demo 需要 Linux（SEEK_HOLE/SEEK_DATA 与 fallocate 的 FALLOC_FL_*）\n");
    return 0;
#else
    char path[] = "/tmp/sparse_holes_XXXXXX";
    int fd = mkstemp(path);
    if (fd < 0) {
        perror("mkstemp");
        return 1;
    }
    unlink(path);

    char buf[BLK];
    memset(buf, 'A', sizeof(buf));

    printf("== 1. 造一个带洞的文件 ==\n");
    if (pwrite(fd, buf, BLK, 0) != BLK) { perror("pwrite"); return 1; }
    if (pwrite(fd, buf, BLK, 2 * (off_t)BLK) != BLK) { perror("pwrite"); return 1; }
    show_stat("初始", fd);

    printf("== 2. 沿 data/hole 交替走一遍 ==\n");
    xseek(fd, 0, SEEK_DATA, "SEEK_DATA");
    xseek(fd, 0, SEEK_HOLE, "SEEK_HOLE");
    xseek(fd, BLK, SEEK_DATA, "SEEK_DATA");
    xseek(fd, BLK, SEEK_HOLE, "SEEK_HOLE");
    xseek(fd, BLK + 100, SEEK_HOLE, "SEEK_HOLE");  /* 洞中间：原样返回 */
    xseek(fd, 2 * (off_t)BLK, SEEK_HOLE, "SEEK_HOLE"); /* 末尾隐式洞 */

    printf("== 3. 越过 EOF 会拿 ENXIO ==\n");
    xseek(fd, 3 * (off_t)BLK + 1, SEEK_DATA, "SEEK_DATA");
    xseek(fd, 3 * (off_t)BLK + 1, SEEK_HOLE, "SEEK_HOLE");

    printf("== 4. 打洞：size 不变、blocks 变小 ==\n");
    if (fallocate(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, 0, BLK) != 0) {
        printf("  fallocate(PUNCH_HOLE) 失败 errno=%d (%s)\n",
               errno, strerror(errno));
    } else {
        show_stat("打洞后", fd);
    }

    printf("== 5. PUNCH_HOLE 不带 KEEP_SIZE → EINVAL ==\n");
    if (fallocate(fd, FALLOC_FL_PUNCH_HOLE, 0, BLK) != 0) {
        printf("  如预期失败 errno=%d (%s)\n", errno, strerror(errno));
    } else {
        printf("  未失败（该文件系统可能放宽了要求）\n");
    }

    printf("== 6. COLLAPSE_RANGE 的粒度与 EOF 约束 ==\n");
    if (ftruncate(fd, 5 * (off_t)BLK) != 0) { perror("ftruncate"); }
    if (fallocate(fd, FALLOC_FL_COLLAPSE_RANGE, BLK, 100) != 0) {
        printf("  粒度不整 -> errno=%d (%s)\n", errno, strerror(errno));
    }
    if (fallocate(fd, FALLOC_FL_COLLAPSE_RANGE, 4 * (off_t)BLK, BLK) != 0) {
        printf("  触及 EOF -> errno=%d (%s)\n", errno, strerror(errno));
    }
    if (fallocate(fd, FALLOC_FL_COLLAPSE_RANGE, BLK, BLK) == 0) {
        show_stat("collapse", fd);
    } else {
        printf("  collapse 失败 errno=%d (%s)\n", errno, strerror(errno));
    }

    close(fd);
    return 0;
#endif
}
