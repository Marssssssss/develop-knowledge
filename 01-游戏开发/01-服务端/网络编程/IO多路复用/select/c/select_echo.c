/*
 * select_echo.c — Minimal TCP echo server using POSIX select()
 *
 * Build (POSIX):
 *     gcc -Wall -Wextra -O2 select_echo.c -o select_echo
 *
 * Build (Windows / MSVC):
 *     cl /W4 /O2 select_echo.c ws2_32.lib
 *
 * Build (Windows / MinGW):
 *     gcc -Wall -Wextra -O2 select_echo.c -o select_echo.exe -lws2_32
 *
 * Run:
 *     ./select_echo 9000
 *
 * Test (any of):
 *     nc localhost 9000
 *     python3 ../python/client.py
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
  #include <winsock2.h>
  typedef int socklen_t;
  #pragma comment(lib, "ws2_32.lib")
  #define close closesocket
  #define perror_win(msg) fprintf(stderr, "%s: WSA err=%d\n", msg, WSAGetLastError())
#else
  #include <unistd.h>
  #include <errno.h>
  #include <arpa/inet.h>
  #include <sys/socket.h>
  #include <sys/select.h>
  #define perror_win(msg) perror(msg)
#endif

#define MAX_CLIENTS 64
#define BUF_SIZE    4096

int main(int argc, char **argv) {
    int port = (argc > 1) ? atoi(argv[1]) : 9000;

#ifdef _WIN32
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        fprintf(stderr, "WSAStartup failed\n");
        return 1;
    }
#endif

    /* ---------- 1. Create listening socket ---------- */
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) { perror_win("socket"); return 1; }

    int opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, (char *)&opt, sizeof(opt));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port        = htons((unsigned short)port);

    if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror_win("bind"); return 1;
    }
    if (listen(srv, 16) < 0) {
        perror_win("listen"); return 1;
    }
    printf("select_echo listening on :%d (max %d clients)\n", port, MAX_CLIENTS);

    /* ---------- 2. Track all sockets in fd_set ---------- */
    fd_set allset, rset;
    FD_ZERO(&allset);
    FD_SET(srv, &allset);
    int maxfd = srv;

    char buf[BUF_SIZE];

    /* ---------- 3. Main loop ---------- */
    for (;;) {
        rset = allset;
        struct timeval tv = { 1, 0 };   /* 1s timeout allows graceful Ctrl+C */

        int nready = select(maxfd + 1, &rset, NULL, NULL, &tv);
        if (nready < 0) {
#ifdef _WIN32
            perror_win("select"); break;
#else
            if (errno == EINTR) continue;
            perror("select"); break;
#endif
        }
        if (nready == 0) continue;     /* timeout, no event */

        /* Iterate every fd in [0, maxfd]; FD_ISSET tells us which fired */
        for (int fd = 0; fd <= maxfd; fd++) {
            if (!FD_ISSET(fd, &rset)) continue;

            if (fd == srv) {
                /* ---------- 4. New connection ---------- */
                struct sockaddr_in cli;
                socklen_t len = sizeof(cli);
                int cfd = accept(srv, (struct sockaddr *)&cli, &len);
                if (cfd < 0) { perror_win("accept"); continue; }
                FD_SET(cfd, &allset);
                if (cfd > maxfd) maxfd = cfd;
                printf("+ client fd=%d from %s:%d\n",
                       cfd, inet_ntoa(cli.sin_addr), ntohs(cli.sin_port));
            } else {
                /* ---------- 5. Existing client readable ---------- */
                int n = (int)recv(fd, buf, sizeof(buf), 0);
                if (n <= 0) {
                    /* n==0 → peer closed; n<0 → error */
                    if (n < 0) perror_win("recv");
                    printf("- client fd=%d closed\n", fd);
                    close(fd);
                    FD_CLR(fd, &allset);
                } else {
                    /* Echo back exactly what we received */
                    int off = 0;
                    while (off < n) {
                        int k = send(fd, buf + off, n - off, 0);
                        if (k <= 0) { perror_win("send"); break; }
                        off += k;
                    }
                }
            }
        }
    }

    close(srv);
#ifdef _WIN32
    WSACleanup();
#endif
    return 0;
}