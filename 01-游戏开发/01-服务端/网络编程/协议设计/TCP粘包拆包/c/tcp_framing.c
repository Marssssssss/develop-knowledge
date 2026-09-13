/*
 * tcp_framing: TLV / length-prefix streaming parser for TCP.
 *
 *   Frame:  ┌──────────┬──────────────┬──────────────┐
 *           │ Magic 4B │ Length 4B BE │ Payload:Len  │
 *           │ "GAME"   │   u32 BE     │  variable    │
 *           └──────────┴──────────────┴──────────────┘
 *
 * Tolerant of 粘包 (multiple frames in one recv) and 拆包
 * (one frame straddling several recvs) using a single-buffer parser.
 *
 *   gcc -O2 -Wall -Wextra tcp_framing.c -o tcp_framing
 *   ./tcp_framing 9000
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdint.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/epoll.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#define MAGIC "GAME"
#define HDR_LEN 8
#define MAX_FRAME (1 << 20)         /* 1 MiB sanity cap */
#define BUF_SIZE 65536

/* ---------------- Wire format helpers ---------------- */
static int write_frame(int fd, const void *payload, uint32_t plen) {
    unsigned char hdr[HDR_LEN];
    memcpy(hdr, MAGIC, 4);
    hdr[4] = (plen >> 24) & 0xff;
    hdr[5] = (plen >> 16) & 0xff;
    hdr[6] = (plen >> 8)  & 0xff;
    hdr[7] = (plen)       & 0xff;
    if (write(fd, hdr, HDR_LEN) != HDR_LEN) return -1;
    if (plen && write(fd, payload, plen) != (ssize_t)plen) return -1;
    return 0;
}

/* ---------------- Streaming parser ---------------- */
/* state==0  waiting for header  (HDR_LEN bytes needed)
 * state==1  waiting for body    (body_len bytes needed)
 * If the magic doesn't match, scan ahead one byte at a time
 * (cheap, only used at connection start). */
struct parser {
    unsigned char buf[BUF_SIZE];
    size_t filled;
    int state;          /* 0 = header pending, 1 = body pending */
    uint32_t body_len;
};

static void parser_init(struct parser *p) { memset(p, 0, sizeof(*p)); }

/* Append `n` bytes into the parser's buffer and try to extract a complete body. */
static void parser_feed(struct parser *p, const unsigned char *src, size_t n) {
    /* grow window: copy in pieces to satisfy BUF_SIZE limit */
    while (n > 0) {
        size_t space = sizeof(p->buf) - p->filled;
        size_t take = n < space ? n : space;
        memcpy(p->buf + p->filled, src, take);
        p->filled += take;
        src += take; n -= take;

        for (;;) {
            if (p->state == 0) {
                /* need HDR_LEN bytes */
                if (p->filled < HDR_LEN) break;
                if (memcmp(p->buf, MAGIC, 4) != 0) {
                    /* resync: drop 1 byte */
                    memmove(p->buf, p->buf + 1, p->filled - 1);
                    p->filled -= 1;
                    continue;
                }
                p->body_len = ((uint32_t)p->buf[4] << 24) |
                              ((uint32_t)p->buf[5] << 16) |
                              ((uint32_t)p->buf[6] << 8)  |
                              ((uint32_t)p->buf[7]);
                if (p->body_len == 0 || p->body_len > MAX_FRAME) {
                    /* bad length: reset and resync */
                    p->filled = 0;
                    continue;
                }
                memmove(p->buf, p->buf + HDR_LEN, p->filled - HDR_LEN);
                p->filled -= HDR_LEN;
                p->state = 1;
            }
            if (p->state == 1) {
                if (p->filled < p->body_len) break;     /* partial body, wait */
                /* body complete; consumer takes via parser_take */
                break;
            }
        }
    }
}

/* Pop the next complete body. Returns body length, copies bytes to `out`. */
static uint32_t parser_take(struct parser *p, unsigned char *out, uint32_t cap) {
    if (p->state != 1 || p->filled < p->body_len) return 0;
    uint32_t n = p->body_len;
    if (n > cap) n = cap;
    memcpy(out, p->buf, n);
    memmove(p->buf, p->buf + n, p->filled - n);
    p->filled -= n;
    p->state = 0;
    p->body_len = 0;
    return n;
}

/* ---------------- Server: epoll-based ---------------- */
struct conn {
    int fd;
    struct parser parser;
};

int main(int argc, char *argv[]) {
    int port = (argc > 1) ? atoi(argv[1]) : 9000;
    int lfd = socket(AF_INET, SOCK_STREAM, 0);
    int on = 1;
    setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));
    struct sockaddr_in a = { 0 };
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_ANY);
    a.sin_port = htons(port);
    if (bind(lfd, (struct sockaddr *)&a, sizeof(a)) < 0 ||
        listen(lfd, 16) < 0) { perror("listen"); return 1; }

    int ep = epoll_create1(0);
    struct epoll_event ev = { .events = EPOLLIN, .data.ptr = NULL };
    epoll_ctl(ep, EPOLL_CTL_ADD, lfd, &ev);

    printf("tcp_framing listening on :%d\n", port);
    struct epoll_event events[64];
    for (;;) {
        int nev = epoll_wait(ep, events, 64, 1000);
        for (int i = 0; i < nev; i++) {
            /* listen socket */
            if (events[i].data.ptr == NULL) {
                int cfd = accept(lfd, NULL, NULL);
                if (cfd < 0) continue;
                struct conn *c = calloc(1, sizeof(*c));
                c->fd = cfd;
                parser_init(&c->parser);
                ev.data.ptr = c;
                ev.events = EPOLLIN;
                epoll_ctl(ep, EPOLL_CTL_ADD, cfd, &ev);
                continue;
            }
            struct conn *c = events[i].data.ptr;
            unsigned char tmp[8192];
            ssize_t n = recv(c->fd, tmp, sizeof tmp, 0);
            if (n == 0) { close(c->fd); free(c); continue; }
            if (n < 0) continue;

            parser_feed(&c->parser, tmp, (size_t)n);
            /* drain all complete bodies out of parser */
            for (;;) {
                unsigned char body[1 << 16];
                uint32_t blen = parser_take(&c->parser, body, sizeof(body));
                if (blen == 0) break;
                printf("fd=%d frame len=%u '%.*s'\n",
                       c->fd, blen, (int)blen, (char *)body);
                write_frame(c->fd, body, blen);
            }
        }
    }
    close(ep);
    close(lfd);
    return 0;
}
