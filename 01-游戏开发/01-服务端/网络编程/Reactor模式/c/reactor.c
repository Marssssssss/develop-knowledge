/*
 * reactor: single-threaded event-driven game server framework.
 * Built on epoll (Linux), with a clean reactor pattern API:
 *   - register/deregister a fd with the reactor
 *   - associate any user-defined state (e.g. player session)
 *   - dispatch read/write events to user handlers
 *
 * The demo registers a listening socket + echo handlers and reuses this
 * pattern to echo everything back to the client.
 *
 *   gcc -O2 -Wall -Wextra reactor.c -o reactor
 *   ./reactor 9000
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/epoll.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#define MAX_EVENTS 64
#define BUF_SIZE 4096

/* Forward declaration of session struct used by user handlers */
struct session {
    int fd;
    int listen;
    char buf[BUF_SIZE];
    int len;
    int closed;
};

/* ---- Reactor API ----
 * The user handler is called whenever:
 *   - fd has EPOLLIN  and read_handler must drain the socket
 *   - fd has EPOLLOUT and write_handler may try sending
 * Handlers must be non-blocking; if no work, return without blocking.
 */
typedef struct reactor {
    int epfd;
    /* Simple fd -> session lookup (real engine: hash map) */
    struct session *sessions[1024];
} reactor_t;

static int reactor_init(reactor_t *r) {
    r->epfd = epoll_create1(0);
    if (r->epfd < 0) return -1;
    memset(r->sessions, 0, sizeof(r->sessions));
    return 0;
}

static int reactor_register(reactor_t *r, int fd, uint32_t events,
                            struct session *s) {
    if (fd < 0 || fd >= 1024) return -1;
    r->sessions[fd] = s;
    struct epoll_event ev = { .events = events, .data.ptr = s };
    return epoll_ctl(r->epfd, EPOLL_CTL_ADD, fd, &ev);
}

static int reactor_modify(reactor_t *r, int fd, uint32_t events,
                          struct session *s) {
    struct epoll_event ev = { .events = events, .data.ptr = s };
    return epoll_ctl(r->epfd, EPOLL_CTL_MOD, fd, &ev);
}

static int reactor_unregister(reactor_t *r, int fd, struct session *s) {
    s->closed = 1;
    r->sessions[fd] = NULL;
    return epoll_ctl(r->epfd, EPOLL_CTL_DEL, fd, NULL);
}

/* ---- User-defined handlers ---- */

static void on_accept(reactor_t *r, struct session *listen_s) {
    for (;;) {
        int cfd = accept4(listen_s->fd, NULL, NULL, SOCK_NONBLOCK);
        if (cfd < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) return;
            perror("accept4"); return;
        }
        struct session *c = calloc(1, sizeof(*c));
        c->fd = cfd;
        c->listen = 0;
        reactor_register(r, cfd, EPOLLIN, c);
        printf("accept fd=%d\n", cfd);
    }
}

static void on_read(reactor_t *r, struct session *s) {
    ssize_t n = recv(s->fd, s->buf + s->len, sizeof(s->buf) - s->len, 0);
    if (n == 0) {
        printf("peer closed fd=%d\n", s->fd);
        close(s->fd);
        reactor_unregister(r, s->fd, s);
        free(s);
        return;
    }
    if (n < 0) {
        if (errno != EAGAIN) perror("recv");
        return;
    }
    s->len += (int)n;
    /* Best-effort echo: in production, parse the buffer against a protocol */
    ssize_t sent = send(s->fd, s->buf, s->len, 0);
    if (sent > 0) {
        if (sent < s->len) {
            memmove(s->buf, s->buf + sent, s->len - sent);
            s->len -= (int)sent;
        } else {
            s->len = 0;
        }
    }
}

int main(int argc, char *argv[]) {
    int port = (argc > 1) ? atoi(argv[1]) : 9000;

    /* listen socket */
    int lfd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0);
    int on = 1;
    setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));
    struct sockaddr_in addr = { 0 };
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(port);
    if (bind(lfd, (struct sockaddr *)&addr, sizeof(addr)) < 0 ||
        listen(lfd, 16) < 0) { perror("listen"); return 1; }

    reactor_t reactor;
    reactor_init(&reactor);

    struct session *listen_s = calloc(1, sizeof(*listen_s));
    listen_s->fd = lfd;
    listen_s->listen = 1;
    reactor_register(&reactor, lfd, EPOLLIN, listen_s);

    printf("reactor listening on :%d (epfd=%d)\n", port, reactor.epfd);

    /* Reactor main loop:
     *   Phase 1: synchronous event demultiplexer
     *   Phase 2: dispatch to handlers
     */
    struct epoll_event events[MAX_EVENTS];
    for (;;) {
        int nev = epoll_wait(reactor.epfd, events, MAX_EVENTS, 1000);
        if (nev < 0) { if (errno == EINTR) continue; perror("epoll_wait"); break; }
        for (int i = 0; i < nev; i++) {
            struct session *s = (struct session *)events[i].data.ptr;
            uint32_t e = events[i].events;
            if (s->closed) continue;
            if (e & (EPOLLERR | EPOLLHUP)) {
                close(s->fd); reactor_unregister(&reactor, s->fd, s); free(s); continue;
            }
            if (s->listen) {
                if (e & EPOLLIN) on_accept(&reactor, s);
            } else {
                if (e & EPOLLIN) on_read(&reactor, s);
            }
        }
    }

    close(reactor.epfd);
    close(lfd);
    return 0;
}
