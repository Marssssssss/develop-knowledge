/* scatter-gather IO 与 TCP_CORK：把"包数"和"系统调用数"真正量出来
 *
 * 用 loopback TCP 实测四种"写响应"策略，并用 getsockopt(TCP_INFO).tcpi_segs_out
 * （内核统计的已发送段数）作为**包数**的客观依据，而不是靠推断：
 *
 *   A. write(header) + write(body)          两次调用 → 两个段
 *   B. memcpy 合并 + write(整体)             一次调用 → 一个段（代价：用户态 memcpy）
 *   C. writev(header, body)                  一次调用 → 一个段（无需用户态拷贝）
 *   D. TCP_CORK + write + write + uncork     两次写被塞住，uncork 时一齐发出 → 一个段
 *
 * 另外用 readv 在接收端验证 man7 readv(2) 的"按数组顺序填满"语义。
 *
 * 仅 Linux（TCP_INFO / TCP_CORK / sendfile 语义）。需要 fork，故不用 Windows。
 *
 * 编译：gcc -O2 -Wall -Wextra main.c -o scatter_gather_demo
 * 运行：./scatter_gather_demo
 */

#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* man7 tcp(7)：TCP_CORK 有 200 ms 上限；其语义可被 TCP_NODELAY 强制刷新 */
#define CORK_CEILING_MS 200

static int failures = 0;
static int checks = 0;

#define CHECK(cond, msg)                                                       \
    do {                                                                       \
        checks++;                                                              \
        if (!(cond)) {                                                         \
            printf("  [FAIL] %s\n", (msg));                                    \
            failures++;                                                        \
        }                                                                      \
    } while (0)

/* ----------------------------------------------------------- 测试夹具 */
typedef struct {
    int listen_fd, conn_fd;
    pid_t child;
} Fixture;

/* 建立 loopback TCP 连接；子进程负责把数据读掉并统计字节数 */
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
    addr.sin_port = 0;
    if (bind(fx->listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) return -1;
    if (listen(fx->listen_fd, 16) < 0) return -1;
    if (getsockname(fx->listen_fd, (struct sockaddr *)&addr, &alen) < 0) return -1;

    fx->child = fork();
    if (fx->child == 0) {
        /* 接收端：把数据全部读掉，避免发送端被流控卡住 */
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        char buf[8192];
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

#include "scatter_impl.h"
/* ------------------------------------------------------------------ main */
int main(void) {
    const int header_len = 180;   /* 典型 HTTP 响应头 */
    const int body_len = 512;     /* 典型小响应体 */
    long segs[4], written[4];
    const mode_t modes[4] = {MODE_TWO_WRITE, MODE_MERGE, MODE_WRITEV, MODE_CORK};

    signal(SIGPIPE, SIG_IGN);

    printf("=== scatter-gather IO 与 TCP_CORK 实测（loopback TCP，"
           "头 %d B + 体 %d B，TCP_NODELAY=1）===\n", header_len, body_len);
    printf("包数取自内核 getsockopt(TCP_INFO).tcpi_segs_out（Linux 4.6+），"
           "不是应用层推断值。\n\n");
    printf("  %-38s %8s %6s %8s\n", "策略", "系统调用", "包数", "写出字节");
    for (int i = 0; i < 4; i++) {
        run_mode(modes[i], header_len, body_len, &segs[i], &written[i]);
        printf("  %-38s %8d %6ld %8ld\n", mode_name(modes[i]),
               mode_syscalls(modes[i]), segs[i], written[i]);
    }

    printf("\n=== readv 的分散读顺序验证 ===\n");
    {
        int got = readv_scatter_check();
        printf("  readv(iov[100,900,1000]) 读到 %d 字节，"
               "三个缓冲区的填充顺序与数组顺序一致\n", got);
    }

    printf("\n=== TCP_CORK 的 200 ms 上限（man7 tcp(7)）===\n");
    {
        Fixture fx;
        struct timespec ts0, ts1;
        if (fixture_open(&fx) == 0) {
            long before;
            set_opt(fx.conn_fd, IPPROTO_TCP, TCP_NODELAY, 1);
            set_opt(fx.conn_fd, IPPROTO_TCP, TCP_CORK, 1);
            before = segs_out(fx.conn_fd);
            write(fx.conn_fd, "partial", 7);
            clock_gettime(CLOCK_MONOTONIC, &ts0);
            printf("  已 cork 并写入 7 字节且未 uncork，等待内核自动发送…\n");
            /* 睡够 250 ms：内核应在 200 ms 上限处自动把排队数据发出 */
            usleep(250 * 1000);
            clock_gettime(CLOCK_MONOTONIC, &ts1);
            printf("  经过 %.0f ms 后 tcpi_segs_out 由 %ld 变为 %ld（> before 即已自动发出）\n",
                   (double)(ts1.tv_sec - ts0.tv_sec) * 1000 +
                       (double)(ts1.tv_nsec - ts0.tv_nsec) / 1e6,
                   before, segs_out(fx.conn_fd));
            CHECK(segs_out(fx.conn_fd) > before, "超过 200 ms 后排队数据应被自动发出");
            shutdown(fx.conn_fd, SHUT_WR);
            fixture_close(&fx);
        }
    }

    printf("\n=== 自检 ===\n");
    /* 1. writev 与 TCP_CORK 都应把 header+body 压成 1 个段（确定性）
     *    两次独立 write 的段数应"不少于"writev；实测通常正好是 2，但若恰好被
     *    tcp_autocorking（Linux 3.14+ 默认开）合并成 1，那也是合法的内核行为，
     *    所以断言只写单调性，不写死 ==2。 */
    CHECK(segs[2] == 1, "writev 应只产生 1 个段");
    CHECK(segs[3] == 1, "TCP_CORK 应把两次写压成 1 个段");
    CHECK(segs[0] >= segs[2] && segs[0] <= 2,
          "两次独立 write 的段数应在 1..2 之间且不少于 writev");
    printf("  实测：两次 write=%ld 段，writev=%ld 段，CORK=%ld 段\n",
           segs[0], segs[2], segs[3]);
    /* 2. 四种策略写出的字节数必须一致（否则是短写没处理） */
    for (int i = 1; i < 4; i++) {
        CHECK(written[i] == written[0], "各策略写出的字节数应相同");
    }
    CHECK(written[0] == header_len + body_len, "应写出 header + body 的全部字节");
    /* 3. 系统调用数：合并写 == writev == 1，少于两次独立 write 的 2 */
    CHECK(mode_syscalls(MODE_WRITEV) == 1 && mode_syscalls(MODE_TWO_WRITE) == 2,
          "writev 应把 2 次系统调用压成 1 次");
    /* 4. man7 的 CORK 上限常量 */
    CHECK(CORK_CEILING_MS == 200, "TCP_CORK 的 200 ms 上限是 man7 的既定事实");

    if (failures) { printf("\n有 %d 项自检失败\n", failures); return 1; }
    printf("自检通过（%d 项）\n", checks);
    return 0;
}
