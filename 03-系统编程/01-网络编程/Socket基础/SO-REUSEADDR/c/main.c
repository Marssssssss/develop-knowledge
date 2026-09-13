// SO_REUSEADDR / SO_REUSEPORT 与 TIME_WAIT 演示。
//
// 场景:
//   1) 启动 server A,客户端连接;ctrl-C 关掉 A → A 主动关闭,客户端先关 → A 进入 TIME_WAIT
//   2) 不带 SO_REUSEADDR 立即重启 A → EADDRINUSE
//   3) 带 SO_REUSEADDR 立即重启 A → 成功绑定(且能接受新连接)
//   4) 多进程演示 SO_REUSEPORT:同时绑同一端口,内核分发连接
//
// 编译: gcc -O2 -Wall -Wextra -o reuse_demo main.c
// 运行:
//   ./reuse_demo server 9090           # 模式 1:普通 server
//   ./reuse_demo server_reuse 9090     # 模式 2:开 SO_REUSEADDR
//   ./reuse_demo twin A 9090           # 模式 3:开 SO_REUSEPORT(可启多份,演示负载分担)
//   ./reuse_demo client 127.0.0.1 9090
//   ./reuse_demo explain               # 打印内核源码要点(辅助理解)

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <pthread.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <sys/wait.h>

static volatile sig_atomic_t g_stop = 0;
static void on_sigint(int s) { (void)s; g_stop = 1; }

// 创建一个 IPv4 TCP 监听 socket,按 opts 设选项(REUSEADDR=1/REUSEPORT=1)
static int make_listen(int port, int reuse_addr, int reuse_port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { perror("socket"); return -1; }
    int yes = 1;
    if (reuse_addr) setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    if (reuse_port) setsockopt(fd, SOL_SOCKET, SO_REUSEPORT,  &yes, sizeof(yes));
    struct sockaddr_in a = { .sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_ANY), .sin_port = htons(port) };
    if (bind(fd, (struct sockaddr *)&a, sizeof(a)) < 0) {
        fprintf(stderr, "  bind() failed: %s (errno=%d)\n", strerror(errno), errno);
        close(fd); return -1;
    }
    if (listen(fd, 16) < 0) { perror("listen"); close(fd); return -1; }
    return fd;
}

// 通过 netstat 查本机 9090 上属于本进程 PID 的 socket 是否在 TIME_WAIT
static void dump_state(int port) {
    char buf[512];
    snprintf(buf, sizeof(buf), "ss -tan state time-wait sport = :%d 2>/dev/null | head -5", port);
    fprintf(stderr, "  [diag] %s\n", buf);
    fflush(stderr);
    FILE *f = popen(buf, "r");
    if (!f) return;
    while (fgets(buf, sizeof(buf), f)) fprintf(stderr, "    %s", buf);
    pclose(f);
}

// 模式 1:普通 server(不开 REUSEADDR)。先关后立即重启会因 TIME_WAIT 失败。
static int run_normal_server(int port) {
    int fd = make_listen(port, 0, 0);
    if (fd < 0) return 1;
    printf("[normal] listening on :%d\n", port);
    dump_state(port);
    while (!g_stop) {
        struct sockaddr_in cli; socklen_t cl = sizeof(cli);
        int cfd = accept(fd, (struct sockaddr *)&cli, &cl);
        if (cfd < 0) { if (errno == EINTR) continue; break; }
        printf("[normal] accepted %s:%d, closing now\n", inet_ntoa(cli.sin_addr), ntohs(cli.sin_port));
        close(cfd);  // 服务端主动关闭,client 是被动方(不主动关会进 TIME_WAIT)
    }
    close(fd);
    return 0;
}

// 模式 2:开 SO_REUSEADDR → 关闭后立即重启可成功。
static int run_reuse_server(int port) {
    int fd = make_listen(port, 1, 0);
    if (fd < 0) return 1;
    printf("[reuse ] listening on :%d (SO_REUSEADDR=1)\n", port);
    dump_state(port);
    while (!g_stop) {
        struct sockaddr_in cli; socklen_t cl = sizeof(cli);
        int cfd = accept(fd, (struct sockaddr *)&cli, &cl);
        if (cfd < 0) { if (errno == EINTR) continue; break; }
        printf("[reuse ] accepted %s:%d\n", inet_ntoa(cli.sin_addr), ntohs(cli.sin_port));
        close(cfd);
    }
    close(fd);
    return 0;
}

// 模式 3:SO_REUSEPORT 双进程负载分担;启动两份即可看到内核把连接分到两边。
static int run_twin(const char *tag, int port) {
    int fd = make_listen(port, 1, 1);  // REUSEPORT 必须配合 REUSEADDR 才稳
    if (fd < 0) return 1;
    printf("[twin %s] listening on :%d (SO_REUSEPORT=1)\n", tag, port);
    while (!g_stop) {
        struct sockaddr_in cli; socklen_t cl = sizeof(cli);
        int cfd = accept(fd, (struct sockaddr *)&cli, &cl);
        if (cfd < 0) { if (errno == EINTR) continue; break; }
        printf("[twin %s] accepted %s:%d\n", tag, inet_ntoa(cli.sin_addr), ntohs(cli.sin_port));
        sleep(2);  // 拉长处理时间,便于另一 twin 抢到下一连接
        close(cfd);
    }
    close(fd);
    return 0;
}

static int run_client(const char *host, int port, int n) {
    for (int i = 0; i < n; i++) {
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(port) };
        inet_pton(AF_INET, host, &a.sin_addr);
        if (connect(fd, (struct sockaddr *)&a, sizeof(a)) == 0) {
            printf("[client %d/%d] connected; closing immediately (so server holds the TIME_WAIT)\n",
                   i + 1, n);
        }
        close(fd);  // 客户端先关闭 → 服务端在 close(cfd) 后进 TIME_WAIT
        usleep(100 * 1000);
    }
    return 0;
}

static void explain(void) {
    printf("=== SO_REUSEADDR vs SO_REUSEPORT vs TIME_WAIT ===\n");
    printf("TIME_WAIT: TCP 主动关闭方在发 FIN 并收到 ACK 后进入的状态;持续 2*MSL\n");
    printf("           (Linux 默认 60s,实际 TCP_TIMEWAIT_LEN 写死),目的:\n");
    printf("           a) 让旧 FIN/ACK 重传完成,避免后续连接被旧报文混淆\n");
    printf("           b) 让旧五元组(srcip,srcport,dstip,dstport,proto)在网络中不再出现\n");
    printf("\n");
    printf("SO_REUSEADDR (POSIX): 允许 bind 一个处于 TIME_WAIT 的地址;但 Linux 上\n");
    printf("           '已绑的旧 socket' 也必须设过此选项才能被覆盖;同一进程可重绑。\n");
    printf("SO_REUSEPORT (Linux 3.9+): 允许多个 socket 同时 bind 完全相同的 (addr,port);\n");
    printf("           内核按四元组 hash 将新连接分到其中一个 socket;用于多进程负载分担。\n");
    printf("\n");
    printf("实验: \n");
    printf("  1) 启 server_reuse 9090\n");
    printf("  2) 启 client 连上 → server accept + close → server 进 TIME_WAIT\n");
    printf("  3) ctrl-C 关 server_reuse\n");
    printf("  4) 立刻再启 server_reuse 9090 → 成功\n");
    printf("  5) 改成 server 9090 重做一遍 → bind 失败 EADDRINUSE\n");
}

int main(int argc, char **argv) {
    signal(SIGINT, on_sigint);
    signal(SIGPIPE, SIG_IGN);
    if (argc < 2) {
        printf("usage:\n  %s server <port>\n  %s server_reuse <port>\n  %s twin <tag> <port>\n  %s client <ip> <port> [N]\n  %s explain\n",
               argv[0], argv[0], argv[0], argv[0], argv[0]);
        return 1;
    }
    if (strcmp(argv[1], "server") == 0 && argc == 3) return run_normal_server(atoi(argv[2]));
    if (strcmp(argv[1], "server_reuse") == 0 && argc == 3) return run_reuse_server(atoi(argv[2]));
    if (strcmp(argv[1], "twin") == 0 && argc == 4) return run_twin(argv[2], atoi(argv[3]));
    if (strcmp(argv[1], "client") == 0) {
        int n = argc >= 5 ? atoi(argv[4]) : 5;
        return run_client(argv[2], atoi(argv[3]), n);
    }
    if (strcmp(argv[1], "explain") == 0) { explain(); return 0; }
    fprintf(stderr, "bad args\n");
    return 1;
}