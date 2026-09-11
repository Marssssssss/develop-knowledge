/*
 * mmap_demo.c — POSIX mmap(2) 的 4 个核心 demo
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic mmap_demo.c -o mmap_demo
 * 运行: ./mmap_demo
 *
 * 演示:
 *   1. MAP_SHARED 写文件经 mmap + msync 落盘
 *   2. MAP_PRIVATE 写时复制(CoW)不影响源
 *   3. MAP_ANONYMOUS|MAP_SHARED 父子进程共享内存 IPC
 *   4. mmap 后 ftruncate 缩短 → SIGBUS 边界演示
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <signal.h>
#include <errno.h>

#define PSIZE   4096                /* 演示用一页大小 */
#define PATH_F  "data.bin"

/* === demo 1: MAP_SHARED 写文件 ============================= */
static void demo_shared_write(void) {
    printf("\n=== demo 1: MAP_SHARED write + msync ===\n");
    int fd = open(PATH_F, O_RDWR | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) { perror("open"); exit(1); }
    if (ftruncate(fd, PSIZE) < 0) { perror("ftruncate"); exit(1); }

    /* 写入已知初始内容,方便验证 */
    const char *init = "INIT____INIT____INIT____INIT____INIT____INIT____";  /* 60B */
    if (write(fd, init, strlen(init)) != (ssize_t)strlen(init)) {
        perror("write init"); exit(1);
    }

    volatile char *p = mmap(NULL, PSIZE, PROT_READ | PROT_WRITE,
                            MAP_SHARED, fd, 0);
    if (p == MAP_FAILED) { perror("mmap SHARED"); exit(1); }

    /* 第一次写触发 page fault,把该页从文件读入 page cache */
    p[0] = 'A'; p[1] = 'B'; p[2] = '\0';
    printf("p[0..3] = \"%s\" (in-memory after write)\n", (char*)p);

    msync((void*)p, PSIZE, MS_SYNC);     /* 强制落盘 */
    printf("msync(MS_SYNC) done\n");

    /* 重新打开磁盘上文件验证内容 */
    int fd2 = open(PATH_F, O_RDONLY);
    if (fd2 < 0) { perror("reopen"); exit(1); }
    char buf[8] = {0};
    read(fd2, buf, 7);
    printf("file on disk [0..7] = \"%.7s\"\n", buf);  /* 期望 "AB" + "_I..." */

    munmap((void*)p, PSIZE);
    close(fd); close(fd2);
}

/* === demo 2: MAP_PRIVATE 写时复制 ========================== */
static void demo_private_cow(void) {
    printf("\n=== demo 2: MAP_PRIVATE copy-on-write ===\n");
    int fd = open(PATH_F, O_RDONLY);                  /* 用 demo 1 落盘的 "AB_..." */
    if (fd < 0) { perror("open"); exit(1); }

    char *q = mmap(NULL, PSIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE, fd, 0);
    if (q == MAP_FAILED) { perror("mmap PRIVATE"); exit(1); }

    printf("MAP_PRIVATE q[0..3] before write = \"%.3s\"\n", q);
    q[0] = 'Z';                                         /* CoW 触发,只改私有副本 */
    printf("after q[0] = 'Z', q[0..3] = \"%.3s\"\n", q);

    /* 源文件不变:磁盘仍是 demo 1 的 'A' */
    char buf[4] = {0};
    int fd2 = open(PATH_F, O_RDONLY);
    read(fd2, buf, 3);
    printf("disk still contains [0..2] = \"%.3s\" (no Z)\n", buf);
    close(fd2);

    munmap(q, PSIZE);
    close(fd);
}

/* === demo 3: MAP_ANONYMOUS|MAP_SHARED 父子进程 IPC ======= */
static void demo_anon_shared_ipc(void) {
    printf("\n=== demo 3: anonymous MAP_SHARED IPC (parent<->child) ===\n");
    /* 匿名 SHARED:不绑文件,长度按页对齐,内容初始化为 0 */
    long *shm = mmap(NULL, PSIZE, PROT_READ | PROT_WRITE,
                     MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (shm == MAP_FAILED) { perror("mmap ANON"); exit(1); }

    /* 占 4 个 long:counter + flag + 2 个数据槽 */
    shm[0] = 0;                /* counter */
    shm[1] = 0;                /* child_ready flag */

    pid_t pid = fork();
    if (pid < 0) { perror("fork"); exit(1); }
    if (pid == 0) {
        /* 子进程写共享内存 */
        shm[0] = 100;
        shm[2] = 0xDEADBEEF;     /* 数据槽 1 */
        shm[3] = 0xCAFEBABE;     /* 数据槽 2 */
        shm[1] = 1;              /* 通知父:ready */
        _exit(0);
    }
    /* 父进程:等待子进程写完 */
    waitpid(pid, NULL, 0);
    printf("parent sees: counter=%ld, slot1=0x%lX, slot2=0x%lX, ready=%ld\n",
           shm[0], shm[2], shm[3], shm[1]);
    /* 期望 counter=100, slot1=0xDEADBEEF, slot2=0xCAFEBABE, ready=1 */

    munmap(shm, PSIZE);
}

/* === demo 4: mmap 后 ftruncate 缩短 → SIGBUS ================ */
static void demo_truncate_sigbus(void) {
    printf("\n=== demo 4: SIGBUS on access beyond truncated size ===\n");
    int fd = open("trunc_demo.bin", O_RDWR | O_CREAT | O_TRUNC, 0644);
    ftruncate(fd, PSIZE * 2);                /* 2 页 */
    volatile char *p = mmap(NULL, PSIZE * 2, PROT_READ | PROT_WRITE,
                            MAP_SHARED, fd, 0);
    if (p == MAP_FAILED) { perror("mmap"); exit(1); }
    p[0] = 'X';
    printf("before truncate: p[0]='X' OK\n");

    /* 子进程触发 SIGBUS,主进程先解除 SIGBUS 默认行为后优雅退出 */
    pid_t pid = fork();
    if (pid == 0) {
        ftruncate(fd, PSIZE / 2);            /* 缩到半页,第二页已无对应文件页 */
        sleep(0);                            /* 让父进程有机会看到 */
        fprintf(stderr, "child: accessing p[4096] (beyond new size) ...\n");
        fflush(stderr);
        p[PSIZE] = 'Y';                      /* 应触发 SIGBUS */
        fprintf(stderr, "child: unexpectedly returned\n");
        _exit(0);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    if (WIFSIGNALED(status) && WTERMSIG(status) == SIGBUS) {
        printf("parent: child killed by SIGBUS as expected ✓\n");
    } else {
        printf("parent: child exited normally? status=0x%x\n", status);
    }

    munmap((void*)p, PSIZE * 2);
    close(fd);
    unlink("trunc_demo.bin");
}

int main(void) {
    /* SIGBUS/SIGSEGV 的默认行为是 core dump;为 demo 美观,这里恢复默认并忽略 */
    signal(SIGBUS, SIG_DFL);    /* 故意让 SIGBUS 杀死子进程演示 */
    demo_shared_write();
    demo_private_cow();
    demo_anon_shared_ipc();
    demo_truncate_sigbus();
    unlink(PATH_F);
    printf("\nall demos done\n");
    return 0;
}