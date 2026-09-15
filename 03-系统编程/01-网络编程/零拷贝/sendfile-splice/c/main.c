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

/* --------------------------------------------------------------- 测试夹具 */
typedef struct {
    int listen_fd, conn_fd;
    pid_t child;
} Fixture;

/* 建立一个 loopback TCP 连接，子进程在另一端不停 recv 直到对端关闭 */
static int fixture_open(Fixture *fx) {
    struct sockaddr_in addr;
    socklen_t alen = sizeof(addr);
    int one = 1;

    fx->listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fx->listen_fd < 0) return -1;
    setsockopt(fx->listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(PORT);
    if (bind(fx->listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) return -1;
    if (listen(fx->listen_fd, 128) < 0) return -1;
    if (getsockname(fx->listen_fd, (struct sockaddr *)&addr, &alen) < 0) return -1;

    fx->child = fork();
    if (fx->child == 0) {
        /* 接收端：把数据全部读掉并丢弃，避免发送端被流控卡住 */
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        char buf[CHUNK];
        if (fd < 0) _exit(1);
        if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) _exit(2);
        for (;;) {
            ssize_t n = recv(fd, buf, sizeof(buf), 0);
            if (n <= 0) break;
        }
        close(fd);
        _exit(0);
    }
    if (fx->child < 0) return -1;
    fx->conn_fd = accept(fx->listen_fd, NULL, NULL);
    return fx->conn_fd < 0 ? -1 : 0;
}

static void fixture_close(Fixture *fx) {
    int status;
    close(fx->conn_fd);
    close(fx->listen_fd);
    waitpid(fx->child, &status, 0);
}

/* ---------------------------------------------------------------- 四种方式 */
/* 1) 传统 read + write：数据要两次穿过用户态缓冲区 */
static long long send_read_write(int out_fd, const char *path) {
    char *buf = malloc(CHUNK);
    long long total = 0;
    int in_fd = open(path, O_RDONLY);
    if (!buf || in_fd < 0) return -1;
    for (;;) {
        ssize_t n = read(in_fd, buf, CHUNK);
        ssize_t off = 0;
        if (n < 0) { if (errno == EINTR) continue; break; }
        if (n == 0) break;
        while (off < n) {                       /* 写可能短写，必须循环 */
            ssize_t w = write(out_fd, buf + off, (size_t)(n - off));
            if (w < 0) { if (errno == EINTR) continue; break; }
            off += w;
        }
        total += n;
    }
    close(in_fd);
    free(buf);
    return total;
}

/* 2) mmap + write：省掉"内核→用户"的拷贝，但仍要一次"用户→socket"拷贝 */
static long long send_mmap_write(int out_fd, const char *path) {
    struct stat st;
    long long total = 0;
    int in_fd = open(path, O_RDONLY);
    char *p;
    if (in_fd < 0 || fstat(in_fd, &st) < 0) return -1;
    p = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_SHARED, in_fd, 0);
    if (p == MAP_FAILED) { close(in_fd); return -1; }
    for (off_t off = 0; off < st.st_size;) {
        size_t n = (size_t)((st.st_size - off) > CHUNK ? CHUNK : (st.st_size - off));
        ssize_t w = write(out_fd, p + off, n);
        if (w < 0) { if (errno == EINTR) continue; break; }
        off += w;
        total += w;
    }
    munmap(p, (size_t)st.st_size);
    close(in_fd);
    return total;
}

/* 3) sendfile：数据不出内核；out_fd 是 socket，in_fd 必须支持 mmap-like 操作 */
static long long send_sendfile(int out_fd, const char *path) {
    struct stat st;
    off_t off = 0;
    long long total = 0;
    int in_fd = open(path, O_RDONLY);
    if (in_fd < 0 || fstat(in_fd, &st) < 0) return -1;
    while (off < st.st_size) {
        /* offset 非 NULL 时：函数把 off 更新到"最后一个被读字节之后"，且
         * 不修改 in_fd 自己的文件偏移 —— 正好适合循环分块发送。 */
        ssize_t n = sendfile(out_fd, in_fd, &off, CHUNK);
        if (n < 0) {
            if (errno == EINTR) continue;
            if (errno == EINVAL || errno == ENOSYS) {  /* man7 建议回退 */
                fprintf(stderr, "  sendfile 不可用 (%s)，回退 read/write\n",
                        strerror(errno));
                close(in_fd);
                return send_read_write(out_fd, path);
            }
            perror("sendfile");
            break;
        }
        if (n == 0) break;
        total += n;
    }
    close(in_fd);
    return total;
}

/* 4) splice：页引用经管道搬运，两端至少一端必须是管道；socket→socket 只此一途 */
static long long send_splice(int out_fd, const char *path) {
    int pfd[2];
    long long total = 0;
    int in_fd = open(path, O_RDONLY);
    if (in_fd < 0) return -1;
    if (pipe(pfd) < 0) { close(in_fd); return -1; }
    for (;;) {
        ssize_t n = splice(in_fd, NULL, pfd[1], NULL, CHUNK, SPLICE_F_MOVE);
        if (n < 0) { if (errno == EINTR) continue; perror("splice-in"); break; }
        if (n == 0) break;
        ssize_t done = 0;
        while (done < n) {
            ssize_t w = splice(pfd[0], NULL, out_fd, NULL, (size_t)(n - done),
                              SPLICE_F_MOVE | SPLICE_F_MORE);
            if (w < 0) { if (errno == EINTR) continue; perror("splice-out"); break; }
            done += w;
        }
        total += done;
    }
    close(pfd[0]);
    close(pfd[1]);
    close(in_fd);
    return total;
}

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
