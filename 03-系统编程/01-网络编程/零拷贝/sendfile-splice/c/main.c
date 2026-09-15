/* 零拷贝发送路径实测：read+write / mmap+write / sendfile / splice
 *
 * 把一个临时文件通过 loopback TCP 发出去四遍，分别测量：
 *   - 墙钟时间
 *   - 进程用户态 CPU 时间（getrusage，反映 CPU 拷贝的开销）
 *   - /proc/self/io 的 rchar/wchar（内核侧累计读写字节数）
 * 并对照 man7 sendfile.2 与 file_descriptor(7) 的搬运次数模型。
 *
 * 仅 Linux 可用（sendfile/splice/loopback 语义）。需要 fork，故不用 Windows。
 *
 * 编译：gcc -O2 -Wall -Wextra main.c -o zerocopy_demo
 * 运行：./zerocopy_demo [文件大小 MiB，默认 64]
 */

#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/sendfile.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define CHUNK (128 * 1024) /* 每轮搬运的块大小 */
#define PORT 0             /* 0 = 让内核挑端口 */

static int failures = 0;

static void check(int cond, const char *msg) {
    if (!cond) {
        printf("  [FAIL] %s\n", msg);
        failures++;
    }
}

static double now_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static double cpu_sec(void) {
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
    return (double)ru.ru_utime.tv_sec + (double)ru.ru_utime.tv_usec / 1e6 +
           (double)ru.ru_stime.tv_sec + (double)ru.ru_stime.tv_usec / 1e6;
}

/* 读 /proc/self/io 里的某个计数器（字节） */
static long long proc_io(const char *field) {
    FILE *f = fopen("/proc/self/io", "r");
    char line[128];
    long long v = -1;
    size_t n = strlen(field);
    if (!f) return -1;
    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, field, n) == 0 && line[n] == ':') {
            v = atoll(line + n + 1);
            break;
        }
    }
    fclose(f);
    return v;
}

#include "transfer_impl.h"
/* ---------------------------------------------------------------- 驱动器 */
typedef long long (*sender_fn)(int, const char *);

static void run_case(const char *name, sender_fn fn, const char *path,
                     long long size, const char *cpu_copies) {
    Fixture fx;
    double t0, t1, c0, c1;
    long long wchar0, wchar1, sent;

    if (fixture_open(&fx) < 0) { perror("fixture_open"); return; }
    wchar0 = proc_io("wchar");
    c0 = cpu_sec();
    t0 = now_sec();
    sent = fn(fx.conn_fd, path);
    t1 = now_sec();
    c1 = cpu_sec();
    wchar1 = proc_io("wchar");
    shutdown(fx.conn_fd, SHUT_WR);
    fixture_close(&fx);

    if (sent != size) {
        printf("  [WARN] %s 只发了 %lld / %lld 字节\n", name, sent, size);
        failures++;
    }
    printf("  %-14s %7.3f s  CPU %6.3f s  %8.1f MiB/s  wchar +%lld  CPU拷贝=%s\n",
           name, t1 - t0, c1 - c0,
           (t1 - t0) > 0 ? (double)sent / (t1 - t0) / (1024 * 1024) : 0.0,
           (wchar1 > 0 && wchar0 > 0) ? wchar1 - wchar0 : 0, cpu_copies);
}

static void make_file(const char *path, long long size) {
    int fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0644);
    char buf[CHUNK];
    long long left = size;
    memset(buf, 'A', sizeof(buf));
    if (fd < 0) { perror("open temp"); exit(1); }
    while (left > 0) {
        size_t n = (size_t)(left > CHUNK ? CHUNK : left);
        if (write(fd, buf, n) != (ssize_t)n) { perror("write temp"); exit(1); }
        left -= (long long)n;
    }
    close(fd);
}

int main(int argc, char **argv) {
    const char *path = "zerocopy_test.bin";
    long long size = 64LL * 1024 * 1024;
    struct stat st;

    if (argc > 1) size = atoll(argv[1]) * 1024 * 1024;
    signal(SIGPIPE, SIG_IGN);   /* 对端早关时 write/send 会收到 SIGPIPE */

    make_file(path, size);

    printf("=== 零拷贝发送路径实测（文件 %lld MiB，块大小 %d KiB，loopback TCP）===\n",
           size / (1024 * 1024), CHUNK / 1024);
    printf("说明：CPU 拷贝次数来自 man7 sendfile(2)/tcp(7) 与内核搬运模型，"
           "不是运行时测量值；\n      时间与 CPU 时间才是实测。\n\n");

    run_case("read+write", send_read_write, path, size, "2");
    run_case("mmap+write", send_mmap_write, path, size, "1");
    run_case("sendfile", send_sendfile, path, size, "1（无 SG-DMA 时）");
    run_case("splice", send_splice, path, size, "0");

    /* 自检：路径存在性与基本不变量 */
    printf("\n=== 自检 ===\n");
    check(stat(path, &st) == 0 && st.st_size == size, "临时文件大小应等于请求值");
    check(proc_io("wchar") >= 0, "/proc/self/io 应可读，否则 wchar 统计无效");
    check(CHUNK < 0x7ffff000, "块大小必须小于 sendfile 单次上限 0x7ffff000");

    /* man7 sendfile(2)：in_fd 必须支持 mmap-like 操作，因此不能是 socket。
     * 这里用 AF_UNIX socketpair 验证该限制会以失败返回（校验阶段即返回，
     * 不会真的搬数据，所以不会阻塞）。 */
    {
        int sv[2];
        if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0) {
            errno = 0;
            ssize_t r = sendfile(sv[1], sv[0], NULL, 1);
            check(r < 0 && (errno == EINVAL || errno == ENOSYS),
                  "sendfile 的 in_fd 为 socket 时应以 EINVAL/ENOSYS 失败");
            close(sv[0]);
            close(sv[1]);
        }
    }

    unlink(path);
    if (failures) { printf("有 %d 项自检失败\n", failures); return 1; }
    printf("自检通过\n");
    return 0;
}
