/* TCP 连接建立的两条队列：半连接（SYN）队列 与 全连接（accept）队列
 *
 * 用离散事件状态机复现 man7 listen(2) + 内核 ip-sysctl 文档描述的语义：
 *   - Linux 2.2 起 backlog 限制【全连接队列】，不是半连接队列；
 *   - 全连接队列上限 = min(listen backlog, net.core.somaxconn)（静默取小）；
 *   - 半连接队列上限 = net.ipv4.tcp_max_syn_backlog（每监听器）；
 *   - 全连接队列满时 tcp_abort_on_overflow=0（默认）→ 忽略最终 ACK 让客户端
 *     重传（自愈）；=1 → 直接回 RST；
 *   - 半连接队列满时 tcp_syncookies=1（默认）→ 改用 SYN Cookie，不占队列。
 *
 * 末尾附带 Linux 专属的一段真实 sockect 检查（读 somaxconn / /proc/net/tcp），
 * 非 Linux 平台自动跳过。
 *
 * 编译：gcc -O2 -Wall -Wextra -pedantic main.c -o backlog_demo
 * 运行：./backlog_demo
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define BYTES_PER_SYN_RECV 304   /* 内核文档：单个 SYN_RECV 约 304 字节 */
#define SYN_BACKLOG_FLOOR 128    /* 低内存机器上 tcp_max_syn_backlog 最小值 */
#define SOMAXCONN_MODERN 4096    /* Linux 5.4 起 somaxconn 默认值（更早 128） */
#define SYNACK_RETRIES 5         /* tcp_synack_retries 默认 5 */

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

/* ------------------------------------------------------------ 监听套接字 */
#include "backlog_impl.h"
/* ------------------------------------------------------------------ 自检 */
static void self_check(void) {
    Listener a, slow, hard, off, on;
    long slow_accepted;

    /* 1. 有效上限 = min(backlog, somaxconn) */
    listener_init(&a, 4, 511, 128, 1024, 1, 0);
    CHECK(accept_limit(&a) == 128, "somaxconn 更小时应取 somaxconn");
    listener_free(&a);
    listener_init(&a, 4, 8, 4096, 1024, 1, 0);
    CHECK(accept_limit(&a) == 8, "backlog 更小时应取 backlog");
    listener_free(&a);

    /* 2. 常量与内核文档一致 */
    CHECK(SOMAXCONN_MODERN == 4096, "Linux 5.4 起 somaxconn 默认 4096");
    CHECK(SYN_BACKLOG_FLOOR == 128, "tcp_max_syn_backlog 低内存最小值 128");

    /* 3. 正常场景：应用及时 accept → 不溢出、全部建连 */
    listener_init(&a, 40, 64, 4096, 1024, 1, 0);
    simulate(&a, 40, 2, 4000, 2, 4, 1);
    CHECK(a.syn_dropped == 0 && a.overflow == 0, "应用及时消费时不应溢出");
    CHECK(a.accepted == 40, "正常场景应 40 个连接全部被 accept");
    listener_free(&a);

    /* 4. 应用太慢 → 全连接队列溢出；默认不发 RST，靠重传自愈 */
    listener_init(&slow, 60, 4, 4096, 1024, 1, 0);
    simulate(&slow, 60, 20, 4000, 2, 4, 1);
    CHECK(slow.overflow > 0, "应用太慢应触发全连接队列溢出");
    CHECK(slow.rst_sent == 0, "默认策略不应发 RST");
    CHECK(slow.recovered > 0, "被忽略的 ACK 应能靠重传自愈");
    slow_accepted = slow.accepted;
    listener_free(&slow);

    /* 5. abort_on_overflow=1 → 每次溢出都回 RST，失去自愈机会 */
    listener_init(&hard, 60, 4, 4096, 1024, 1, 1);
    simulate(&hard, 60, 20, 4000, 2, 4, 1);
    CHECK(hard.rst_sent == hard.overflow, "abort_on_overflow=1 时每次溢出都应回 RST");
    CHECK(hard.recovered == 0, "回 RST 的连接不可能自愈");
    CHECK(hard.accepted < slow_accepted, "发 RST 的版本最终建连数应更少");
    listener_free(&hard);

    /* 6. SYN 洪泛 + syncookies 关 → 半连接队列溢出后丢 SYN */
    listener_init(&off, 400, 8, 4096, 4, 0, 0);
    simulate(&off, 400, 1000, 300, 2, 4, 20);
    CHECK(off.syn_dropped > 0, "syncookies 关闭时应丢 SYN");
    CHECK(off.max_syn_q <= 4, "半连接队列不应超过 tcp_max_syn_backlog");
    CHECK(off.syncookie_issued == 0, "关闭时不应发 SYN Cookie");
    listener_free(&off);

    /* 7. SYN 洪泛 + syncookies 开 → 不丢 SYN、不占队列 */
    listener_init(&on, 400, 8, 4096, 4, 1, 0);
    simulate(&on, 400, 1000, 300, 2, 4, 20);
    CHECK(on.syn_dropped == 0, "syncookies 开启时不应丢 SYN");
    CHECK(on.syncookie_issued > 0, "溢出部分应改用 SYN Cookie");
    CHECK(on.max_syn_q <= 4, "SYN Cookie 不占半连接队列");
    listener_free(&on);

    /* 8. 单调性：队列上限调大，溢出次数不增 */
    {
        long prev = -1;
        int bl[4] = {4, 8, 16, 64};
        int monotone = 1, first = -1, last = -1;
        for (int i = 0; i < 4; i++) {
            Listener r;
            listener_init(&r, 60, bl[i], 4096, 1024, 1, 0);
            simulate(&r, 60, 20, 4000, 2, 4, 1);
            if (prev >= 0 && r.overflow > prev) monotone = 0;
            prev = r.overflow;
            if (i == 0) first = r.overflow;
            if (i == 3) last = r.overflow;
            listener_free(&r);
        }
        CHECK(monotone, "队列上限调大后溢出次数不应上升");
        CHECK(last == 0 && first > 0, "backlog=64 应不再溢出，backlog=4 应明显溢出");
    }

    /* 9. somaxconn 是隐形天花板 */
    {
        Listener capped, big;
        listener_init(&capped, 400, 1024, 128, 1024, 1, 0);
        simulate(&capped, 400, 500, 3000, 2, 4, 1);
        listener_init(&big, 400, 1024, 4096, 1024, 1, 0);
        simulate(&big, 400, 500, 3000, 2, 4, 1);
        CHECK(capped.max_accept_q == 128, "somaxconn=128 应把队列卡在 128");
        CHECK(capped.overflow > 0, "被卡住时应发生溢出");
        CHECK(big.max_accept_q > 128 && big.overflow == 0,
              "somaxconn 放开后队列应能超过 128 且不再溢出");
        listener_free(&capped);
        listener_free(&big);
    }

    /* 10. 半连接队列的内存代价 ≈ tcp_max_syn_backlog × 304 B */
    {
        double mem = 65536.0 * BYTES_PER_SYN_RECV / (1024 * 1024);
        CHECK(mem > 18.0 && mem < 20.0, "65536 个 SYN_RECV 约 19 MiB");
        CHECK(SYN_BACKLOG_FLOOR * BYTES_PER_SYN_RECV / 1024 == 38,
              "128 个 SYN_RECV 约 38 KiB");
    }

    printf("[self-check] %d 项断言, %d 项失败\n", checks, failures);
}

/* ------------------------------------------- Linux 专属：真实套接字检查 */
#if defined(__linux__)
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

static long read_int_file(const char *path) {
    FILE *f = fopen(path, "r");
    long v = -1;
    if (f) { if (fscanf(f, "%ld", &v) != 1) v = -1; fclose(f); }
    return v;
}

static void linqlive_check(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr;
    socklen_t alen = sizeof(addr);
    long somaxconn = read_int_file("/proc/sys/net/core/somaxconn");
    long syn_backlog = read_int_file("/proc/sys/net/ipv4/tcp_max_syn_backlog");
    int one = 1;

    printf("\n=== 7) 真实套接字检查（Linux）===\n");
    printf("  /proc/sys/net/core/somaxconn            = %ld\n", somaxconn);
    printf("  /proc/sys/net/ipv4/tcp_max_syn_backlog  = %ld\n", syn_backlog);
    if (fd < 0) { printf("  socket() 失败，跳过\n"); return; }
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = 0;
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        printf("  bind() 失败，跳过\n");
        close(fd);
        return;
    }
    /* 故意传一个极大的 backlog：内核会静默截到 somaxconn */
    listen(fd, 1000000);
    if (getsockname(fd, (struct sockaddr *)&addr, &alen) == 0)
        printf("  listen(fd, 1000000) 的监听端口 = %u\n", ntohs(addr.sin_port));
    printf("  取 min 后的有效上限应为 %ld%s\n", somaxconn,
           somaxconn > 0 && somaxconn < 1000000 ? "（backlog 被 somaxconn 静默截断）" : "");
    printf("  用 `ss -lnt` 看该端口：Recv-Q = 当前全连接队列长度，Send-Q = 队列上限\n");
    CHECK(somaxconn > 0, "应能读到 somaxconn（否则说明在非 Linux 或受限容器里）");
    close(fd);
}
#else
static void linqlive_check(void) {
    printf("\n=== 7) 真实套接字检查：当前平台非 Linux，跳过"
           "（sendfile/splice/somaxconn 都不适用）===\n");
}
#endif

/* ------------------------------------------------------------------ main */
int main(void) {
    self_check();

    printf("\n=== 1) 两条队列的分工（man7 listen(2) + Linux 2.2 语义变更）===\n");
    printf("  半连接队列 (SYN queue)   : 存 SYN_RECV，上限 net.ipv4.tcp_max_syn_backlog\n");
    printf("  全连接队列 (accept queue): 存已 ESTABLISHED、等 accept() 的连接\n");
    printf("                            上限 min(listen backlog, net.core.somaxconn)\n");
    printf("  单个 SYN_RECV 约 %d 字节（内核文档）\n", BYTES_PER_SYN_RECV);

    printf("\n=== 2) 应用消费速度 vs 全连接队列溢出（backlog=4，60 个连接）===\n");
    printf("  %12s %10s %16s %10s %6s %12s %6s\n", "accept 间隔", "建连成功",
           "溢出(ACK被忽略)", "自愈成功", "RST", "最终 accept", "放弃");
    {
        int every[5] = {1, 5, 10, 25, 50};
        for (int i = 0; i < 5; i++) {
            Listener r;
            listener_init(&r, 60, 4, 4096, 1024, 1, 0);
            simulate(&r, 60, every[i], 4000, 2, 4, 1);
            printf("  %12d %10ld %16ld %10ld %6ld %12ld %6ld\n", every[i],
                   r.established, r.overflow, r.recovered, r.rst_sent,
                   r.accepted, r.gave_up);
            listener_free(&r);
        }
    }
    printf("  ← 应用越慢溢出越严重；默认策略靠重传自愈一部分，")
    printf("但重传次数有上限（tcp_synack_retries）。\n");

    printf("\n=== 3) tcp_abort_on_overflow：丢弃 vs RST（backlog=4, accept 间隔=4）===\n");
    for (int abort = 0; abort <= 1; abort++) {
        Listener r;
        listener_init(&r, 30, 4, 4096, 1024, 1, abort);
        simulate(&r, 30, 4, 4000, 2, 4, 1);
        printf("  abort_on_overflow=%d: 溢出 %3ld 次, RST %3ld 次, 自愈 %3ld 个, "
               "最终 accept %3ld 个\n", abort, r.overflow, r.rst_sent,
               r.recovered, r.accepted);
        listener_free(&r);
    }

    printf("\n=== 4) SYN 洪泛：syncookies 的挡板作用（syn_backlog=4, 20 SYN/tick）===\n");
    for (int ck = 0; ck <= 1; ck++) {
        Listener r;
        listener_init(&r, 400, 8, 4096, 4, ck, 0);
        simulate(&r, 400, 1000, 300, 2, 4, 20);
        printf("  tcp_syncookies=%d: 处理 SYN %4ld, 丢弃 %4ld, SYN Cookie %3ld, "
               "半连接队列峰值 %d\n", ck, r.syn_received, r.syn_dropped,
               r.syncookie_issued, r.max_syn_q);
        listener_free(&r);
    }

    printf("\n=== 5) somaxconn 是隐形天花板（应用写 backlog=1024，400 个连接）===\n");
    {
        int smc[2] = {128, 4096};
        for (int i = 0; i < 2; i++) {
            Listener r;
            listener_init(&r, 400, 1024, smc[i], 1024, 1, 0);
            simulate(&r, 400, 500, 3000, 2, 4, 1);
            printf("  net.core.somaxconn=%5d: 有效上限 %5d, 队列峰值 %4d, 溢出 %5ld\n",
                   smc[i], accept_limit(&r), r.max_accept_q, r.overflow);
            listener_free(&r);
        }
    }

    printf("\n=== 6) 线上诊断命令 ===\n");
    printf("  ss -lnt\n");
    printf("  netstat -s | grep -i listen\n");
    printf("  nstat -az TcpExtListenOverflows TcpExtListenDrops\n");
    printf("  cat /proc/sys/net/core/somaxconn\n");
    printf("  cat /proc/sys/net/ipv4/tcp_max_syn_backlog\n");
    printf("  dmesg | grep -i 'SYN flooding'\n");

    linqlive_check();

    if (failures) { printf("\n有 %d 项自检失败\n", failures); return 1; }
    return 0;
}
