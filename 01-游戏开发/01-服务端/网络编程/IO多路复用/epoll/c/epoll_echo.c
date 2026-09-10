/*
 * epoll_echo.c — 基于 epoll 的回显服务器（水平触发 LT 模式）
 *
 * 演示 epoll 三步工作流（依据 man7.org epoll(7) / epoll_ctl(2)）：
 *   1. epoll_create1() 创建 epoll 实例（内核中的"容器"）
 *   2. epoll_ctl(EPOLL_CTL_ADD)  把监听 socket 加入 interest list
 *   3. epoll_wait()              从 ready list 取出就绪 fd 处理
 *
 * 编译：gcc -O2 -Wall -Wextra -o epoll_echo epoll_echo.c
 * 运行：./epoll_echo 9000
 * 测试：另一个终端  telnet 127.0.0.1 9000 或  nc 127.0.0.1 9000
 */
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <unistd.h>

#define MAX_EVENTS 64
#define BUF_SIZE   4096

/* 统一死亡出口：出错时打印 errno 对应消息 */
static void die(const char *msg)
{
    perror(msg);
    exit(EXIT_FAILURE);
}

int main(int argc, char *argv[])
{
    int port = (argc > 1) ? atoi(argv[1]) : 9000;

    /* ---- 步骤 0：创建非阻塞监听 socket ----
     * LT 模式下阻塞 socket 通常也能工作，但非阻塞是
     * epoll（尤其 ET 模式）官方推荐的做法。 */
    int listen_fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0);
    if (listen_fd < 0)
        die("socket");

    int on = 1;
    if (setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof on) < 0)
        die("setsockopt");

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons((unsigned short)port);
    if (bind(listen_fd, (struct sockaddr *)&addr, sizeof addr) < 0)
        die("bind");
    if (listen(listen_fd, SOMAXCONN) < 0)
        die("listen");

    /* ---- 步骤 1：创建 epoll 实例 ----
     * epoll_create1(EPOLL_CLOEXEC)：fd 在 exec 时自动关闭。
     * 旧接口 epoll_create(size) 的 size 参数已无意义，仅为兼容。 */
    int epfd = epoll_create1(EPOLL_CLOEXEC);
    if (epfd < 0)
        die("epoll_create1");

    /* ---- 步骤 2：注册监听 fd 到 interest list ----
     * events 是位掩码；data 是内核原样保存、就绪时原样返回的数据。
     * 这里放 fd 本身，方便从 epoll_wait 的结果里取回。 */
    struct epoll_event ev;
    memset(&ev, 0, sizeof ev);
    ev.events = EPOLLIN;          /* 可读事件（有新连接到达也是"可读"） */
    ev.data.fd = listen_fd;
    if (epoll_ctl(epfd, EPOLL_CTL_ADD, listen_fd, &ev) < 0)
        die("epoll_ctl(ADD listen_fd)");

    printf("epoll echo server (LT) listening on port %d\n", port);
    fflush(stdout);

    struct epoll_event events[MAX_EVENTS];

    /* ---- 步骤 3：事件循环 ---- */
    for (;;) {
        /* timeout = -1：无限阻塞直到有事件（返回就绪 fd 个数） */
        int n = epoll_wait(epfd, events, MAX_EVENTS, -1);
        if (n < 0) {
            if (errno == EINTR)   /* 被信号打断：重试即可 */
                continue;
            die("epoll_wait");
        }

        for (int i = 0; i < n; i++) {
            if (events[i].data.fd == listen_fd) {
                /* 监听 fd 就绪 → accept 新连接。
                 * LT 模式下若一次没 accept 完，下次 epoll_wait 仍会报告，
                 * 但循环 accept 到 EAGAIN 是更高效的习惯写法。 */
                for (;;) {
                    struct sockaddr_in cli;
                    socklen_t len = sizeof cli;
                    int conn_fd = accept(listen_fd,
                                         (struct sockaddr *)&cli, &len);
                    if (conn_fd < 0) {
                        if (errno == EAGAIN || errno == EWOULDBLOCK)
                            break;            /* 本轮连接已取完 */
                        if (errno == EINTR)
                            continue;
                        die("accept");
                    }
                    /* 新连接也设为非阻塞，并注册进 interest list */
                    int flags = fcntl(conn_fd, F_GETFL, 0);
                    if (flags >= 0)
                        fcntl(conn_fd, F_SETFL, flags | O_NONBLOCK);
                    ev.events = EPOLLIN | EPOLLRDHUP; /* RDHUP: 对端关闭写半 */
                    ev.data.fd = conn_fd;
                    if (epoll_ctl(epfd, EPOLL_CTL_ADD, conn_fd, &ev) < 0)
                        die("epoll_ctl(ADD conn_fd)");
                    printf("client connected: fd=%d\n", conn_fd);
                    fflush(stdout);
                }
            } else {
                /* 已连接 fd 就绪 → 读并原样写回 */
                int fd = events[i].data.fd;
                char buf[BUF_SIZE];

                if (events[i].events & (EPOLLERR | EPOLLHUP)) {
                    /* EPOLLERR/EPOLLHUP 无需注册也会被上报 */
                    goto close_fd;
                }

                for (;;) {
                    ssize_t r = read(fd, buf, sizeof buf);
                    if (r > 0) {
                        /* 简化处理：忽略 SIGPIPE（写端已关闭时触发），
                         * demo 中假设写入不阻塞 */
                        ssize_t w = write(fd, buf, (size_t)r);
                        (void)w;
                    } else if (r == 0) {
                        /* 对端关闭：EOF */
                        goto close_fd;
                    } else {
                        if (errno == EINTR)
                            continue;
                        break;   /* EAGAIN：LT 模式下下次仍会通知 */
                    }
                }
                continue;

            close_fd:
                /* 先从 interest list 摘除再 close。
                 * 注意：fd 只有在所有指向同一 open file description
                 * 的副本都关闭后才会真正移出 interest list（epoll(7)）。 */
                epoll_ctl(epfd, EPOLL_CTL_DEL, fd, NULL);
                close(fd);
                printf("client closed: fd=%d\n", fd);
                fflush(stdout);
            }
        }
    }
    /* 不可达；epfd 与 listen_fd 由进程退出时内核回收 */
}
