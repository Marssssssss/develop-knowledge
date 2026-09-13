// TCP Keepalive 演示 — 开启 SO_KEEPALIVE + 三个 TCP_KEEP* 参数并回显
// 默认 Linux: tcp_keepalive_time = 7200s (2h), tcp_keepalive_intvl = 75s, tcp_keepalive_probes = 9
// 本 demo 把 idle 改为 5s, intvl 改为 2s, probes 改为 3, 加速探测失败
// 编译: gcc -O2 -Wall -Wextra -o ka_demo main.c
// 运行: ./ka_demo server 9090   +   ./ka_demo client 127.0.0.1 9090

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>

static volatile sig_atomic_t g_stop = 0;
static void on_sigint(int s) { (void)s; g_stop = 1; }

// 读取 keepalive 当前值(SO_KEEPALIVE + 3 个 TCP_KEEP*)
static void dump_keepalive(int fd) {
    int on = 0; socklen_t l = sizeof(on);
    if (getsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &on, &l) < 0) {
        perror("getsockopt SO_KEEPALIVE"); return;
    }
    int idle = 0, intvl = 0, cnt = 0;
    socklen_t il = sizeof(int), nl = sizeof(int), cl = sizeof(int);
    getsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE, &idle, &il);
    getsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &intvl, &nl);
    getsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, &cnt, &cl);
    printf("  SO_KEEPALIVE=%d  TCP_KEEPIDLE=%ds  TCP_KEEPINTVL=%ds  TCP_KEEPCNT=%d\n",
           on, idle, intvl, cnt);
}

// 在已连接 fd 上开启 keepalive + 自定义三参数
static void enable_keepalive(int fd, int idle, int intvl, int cnt) {
    int on = 1;
    if (setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &on, sizeof(on)) < 0)
        perror("setsockopt SO_KEEPALIVE");
    if (setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE,  &idle, sizeof(idle)) < 0)
        perror("setsockopt TCP_KEEPIDLE");
    if (setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &intvl, sizeof(intvl)) < 0)
        perror("setsockopt TCP_KEEPINTVL");
    if (setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT,  &cnt,  sizeof(cnt))  < 0)
        perror("setsockopt TCP_KEEPCNT");
}

static int run_server(int port) {
    int lfd = socket(AF_INET, SOCK_STREAM, 0);
    if (lfd < 0) { perror("socket"); return 1; }
    int yes = 1;
    setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in a = { .sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_ANY), .sin_port = htons(port) };
    if (bind(lfd, (struct sockaddr *)&a, sizeof(a)) < 0) { perror("bind"); return 1; }
    if (listen(lfd, 8) < 0) { perror("listen"); return 1; }
    printf("[server] listening on :%d\n", port);

    struct sockaddr_in cli; socklen_t cl = sizeof(cli);
    int cfd = accept(lfd, (struct sockaddr *)&cli, &cl);
    if (cfd < 0) { perror("accept"); return 1; }
    printf("[server] accepted from %s:%d\n", inet_ntoa(cli.sin_addr), ntohs(cli.sin_port));
    printf("[server] before keepalive:\n");
    dump_keepalive(cfd);

    enable_keepalive(cfd, 5, 2, 3);  // idle=5s, intvl=2s, probes=3 → ~5+6=11s 探测完成
    printf("[server] after keepalive (idle=5s, intvl=2s, probes=3):\n");
    dump_keepalive(cfd);

    char buf[128];
    // recv 直到客户端关闭;若客户端拔网线,keepalive 探测 3 次失败后本 fd 读到 ETIMEDOUT
    printf("[server] waiting for client data (try 'kill -STOP' the client process to test dead-peer detection)...\n");
    while (!g_stop) {
        ssize_t n = recv(cfd, buf, sizeof(buf), 0);
        if (n > 0) { printf("[server] got %zd bytes: %.*s\n", n, (int)n, buf); }
        else if (n == 0) { printf("[server] peer closed\n"); break; }
        else {
            if (errno == EINTR) continue;
            printf("[server] recv error: %s\n", strerror(errno)); break;
        }
    }
    close(cfd); close(lfd);
    return 0;
}

static int run_client(const char *host, int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { perror("socket"); return 1; }
    struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(port) };
    if (inet_pton(AF_INET, host, &a.sin_addr) != 1) { fprintf(stderr, "bad ip\n"); return 1; }
    if (connect(fd, (struct sockaddr *)&a, sizeof(a)) < 0) { perror("connect"); return 1; }
    printf("[client] connected; send 'hello' then sleep 30s (server ka: 5+6=11s should detect dead)\n");
    send(fd, "hello\n", 6, 0);
    sleep(30);  // 模拟"对端进程挂起/拔网线":server 11s 内应收到 ETIMEDOUT
    send(fd, "after sleep\n", 12, 0);
    close(fd);
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage:\n  %s server <port>\n  %s client <ip> <port>\n", argv[0], argv[0]);
        return 1;
    }
    signal(SIGINT, on_sigint);
    signal(SIGPIPE, SIG_IGN);
    if (strcmp(argv[1], "server") == 0) return run_server(atoi(argv[2]));
    if (strcmp(argv[1], "client") == 0 && argc == 4) return run_client(argv[2], atoi(argv[3]));
    fprintf(stderr, "bad args\n"); return 1;
}