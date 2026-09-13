// TCP Echo Server — BSD socket 最小阻塞实现
// 流程: socket(AF_INET, SOCK_STREAM) → bind(:PORT) → listen → accept → recv/send 回环
// 编译: gcc -O2 -Wall -Wextra -o echo_server main.c
// 运行: ./echo_server 9090   (客户端用 nc 127.0.0.1 9090)

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <signal.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#define BUF_SIZE 4096
#define LISTEN_BACKLOG 16

static volatile sig_atomic_t g_stop = 0;
static void on_sigint(int sig) { (void)sig; g_stop = 1; }

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <port>\n", argv[0]);
        return 1;
    }
    int port = atoi(argv[1]);
    if (port <= 0 || port > 65535) { fprintf(stderr, "bad port\n"); return 1; }

    signal(SIGINT, on_sigint);
    signal(SIGPIPE, SIG_IGN);  // 客户端关闭后 send 不被 SIGPIPE 杀死

    // 1) socket
    int lfd = socket(AF_INET, SOCK_STREAM, 0);
    if (lfd < 0) { perror("socket"); return 1; }

    // 2) bind
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons((uint16_t)port);
    if (bind(lfd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind"); close(lfd); return 1;
    }

    // 3) listen(backlog = 16)
    if (listen(lfd, LISTEN_BACKLOG) < 0) {
        perror("listen"); close(lfd); return 1;
    }
    fprintf(stderr, "echo server listening on :%d (Ctrl-C to stop)\n", port);

    // 4) accept loop
    while (!g_stop) {
        struct sockaddr_in cli;
        socklen_t cli_len = sizeof(cli);
        int cfd = accept(lfd, (struct sockaddr *)&cli, &cli_len);
        if (cfd < 0) {
            if (errno == EINTR) continue;
            perror("accept"); break;
        }
        char ip[INET_ADDRSTRLEN] = {0};
        inet_ntop(AF_INET, &cli.sin_addr, ip, sizeof(ip));
        fprintf(stderr, "accept %s:%d (cfd=%d)\n", ip, ntohs(cli.sin_port), cfd);

        // 5) recv/send 回环;read == 0 表示客户端 FIN
        char buf[BUF_SIZE];
        ssize_t n;
        while ((n = recv(cfd, buf, sizeof(buf), 0)) > 0) {
            ssize_t off = 0;
            while (off < n) {
                ssize_t k = send(cfd, buf + off, (size_t)(n - off), 0);
                if (k < 0) {
                    if (errno == EINTR) continue;
                    if (errno == EPIPE) break;  // 客户端先关闭
                    perror("send"); goto end;
                }
                off += k;
            }
        }
        if (n < 0 && errno != EINTR) perror("recv");
    end:
        close(cfd);
        fprintf(stderr, "close cfd=%d\n", cfd);
    }

    close(lfd);
    return 0;
}