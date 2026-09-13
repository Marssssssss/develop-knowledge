/*
 * kqueue_echo: BSD/macOS event-driven echo server using kqueue(2).
 * Demonstrates EVFILT_READ/WRITE, EV_SET macro, EV_ADD/EV_ENABLE/EV_DELETE flags.
 * Compile & run on macOS or FreeBSD:
 *   gcc -O2 -Wall -Wextra kqueue_echo.c -o kqueue_echo
 *   ./kqueue_echo 9000
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/event.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#define MAX_EVENTS 32
#define BUF_SIZE 4096

/*
 * Change fd to nonblocking mode so we can drain socket buffers in a loop
 * until EAGAIN (essential when data exceeds the low-water mark).
 */
static int set_nonblock(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags == -1) return -1;
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

/*
 * Register a kevent with the kqueue.
 * flags=EV_ADD|EV_ENABLE adds and enables the event.
 * EV_DISPATCH disables after one delivery (one-shot semantics).
 */
static void register_event(int kq, int fd, int16_t filter, uint32_t flags,
                           int64_t data, void *udata) {
    struct kevent ev;
    EV_SET(&ev, fd, filter, flags, 0, data, udata);
    if (kevent(kq, &ev, 1, NULL, 0, NULL) == -1) {
        perror("kevent register");
    }
}

int main(int argc, char *argv[]) {
    int port = (argc > 1) ? atoi(argv[1]) : 9000;
    int listen_fd = -1, kq = -1;
    struct sockaddr_in addr;

    /* --- Step 1: create listening socket (blocking, edge-trigger-friendly) --- */
    listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) { perror("socket"); return 1; }

    int on = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));

    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(port);
    if (bind(listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind"); close(listen_fd); return 1;
    }
    if (listen(listen_fd, 16) < 0) {
        perror("listen"); close(listen_fd); return 1;
    }
    set_nonblock(listen_fd);

    /* --- Step 2: create kqueue and register listen_fd EVFILT_READ --- */
    kq = kqueue();
    if (kq < 0) { perror("kqueue"); close(listen_fd); return 1; }

    /*
     * EV_SET(&ev, ident, filter, flags, fflags, data, udata)
     * flags = EV_ADD | EV_ENABLE  (add new event, immediately active)
     * data field for listen filter = backlog (suggested value, kernel may differ)
     */
    register_event(kq, listen_fd, EVFILT_READ, EV_ADD | EV_ENABLE, 16, NULL);

    /*
     * A small connection table sized to MAX_EVENTS.
     * In a real game server, use a hash table keyed by ident or std::unordered_map.
     */
    struct conn { int fd; char buf[BUF_SIZE]; int len; } conns[MAX_EVENTS];
    memset(conns, 0, sizeof(conns));

    printf("kqueue_echo listening on :%d (kq=%d)\n", port, kq);

    /* --- Step 3: event loop (sleeps in kevent) --- */
    struct kevent events[MAX_EVENTS];
    struct timespec timeout = { 1, 0 };  /* 1s wakeup for clean shutdown */

    for (;;) {
        int nev = kevent(kq, NULL, 0, events, MAX_EVENTS, &timeout);
        if (nev < 0) {
            if (errno == EINTR) continue;
            perror("kevent wait");
            break;
        }

        for (int i = 0; i < nev; i++) {
            struct kevent *e = &events[i];
            int64_t filter = e->filter;

            /* --- Case A: error / EOF on a connection --- */
            if (e->flags & EV_ERROR) {
                fprintf(stderr, "EV_ERROR fd=%d errno=%s\n", (int)e->ident,
                        strerror((int)e->data));
                close((int)e->ident);
                continue;
            }

            /* --- Case B: listen socket readable => accept new connection --- */
            if ((int)e->ident == listen_fd && filter == EVFILT_READ) {
                for (;;) {
                    struct sockaddr_in cli;
                    socklen_t cl = sizeof(cli);
                    int cfd = accept(listen_fd, (struct sockaddr *)&cli, &cl);
                    if (cfd < 0) {
                        if (errno == EAGAIN) break;
                        perror("accept"); break;
                    }
                    set_nonblock(cfd);
                    /* Find a free slot */
                    struct conn *slot = NULL;
                    for (int j = 0; j < MAX_EVENTS; j++) {
                        if (conns[j].fd == 0) { slot = &conns[j]; break; }
                    }
                    if (!slot) { close(cfd); continue; }
                    slot->fd = cfd;
                    slot->len = 0;
                    register_event(kq, cfd, EVFILT_READ,
                                   EV_ADD | EV_ENABLE, 0, NULL);
                    /* NOTE_LOWAT in fflags is 0 here; default RCVLOWAT (1 byte) */
                }
                continue;
            }

            /* --- Case C: client socket readable => recv and echo back --- */
            int cfd = (int)e->ident;
            struct conn *slot = NULL;
            for (int j = 0; j < MAX_EVENTS; j++) {
                if (conns[j].fd == cfd) { slot = &conns[j]; break; }
            }
            if (!slot) continue;

            ssize_t n = recv(cfd, slot->buf + slot->len,
                             sizeof(slot->buf) - slot->len, 0);
            if (n == 0) {
                /* peer closed */
                printf("client fd=%d closed\n", cfd);
                close(cfd);
                slot->fd = 0; slot->len = 0;
                continue;
            }
            if (n < 0) {
                if (errno != EAGAIN) perror("recv");
                continue;
            }
            slot->len += (int)n;

            /* Echo: send() may return EAGAIN if socket buffer full; skip for demo */
            ssize_t sent = send(cfd, slot->buf, slot->len, 0);
            if (sent > 0) {
                if (sent < slot->len) {
                    memmove(slot->buf, slot->buf + sent, slot->len - sent);
                    slot->len -= (int)sent;
                } else {
                    slot->len = 0;
                }
            }
        }
    }

    close(kq);
    close(listen_fd);
    return 0;
}
