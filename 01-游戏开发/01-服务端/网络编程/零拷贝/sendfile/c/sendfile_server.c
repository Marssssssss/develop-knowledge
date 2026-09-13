/*
 * sendfile_server: zero-copy file-to-socket using sendfile(2).
 *
 * Serves "static.bin" (we generate it on the fly) to every client,
 * demonstrating the canonical nginx-style pattern:
 *
 *   file_fd = open(path, O_RDONLY)
 *   accept_client()
 *   sendfile(out_fd=socket, in_fd=file_fd, &offset, file_size);
 *
 * Pre-headers prepended via writev() so we still use sendfile for the bulk.
 *
 *   gcc -O2 -Wall -Wextra sendfile_server.c -o sendfile_server
 *   ./sendfile_server 9000 1024      # port, KB
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/uio.h>
#include <sys/epoll.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#define FILE_PATH "/tmp/sendfile_demo.bin"

/* Build a deterministic binary file of N KiB on disk. */
static int build_demo_file(size_t kib) {
    FILE *f = fopen(FILE_PATH, "wb");
    if (!f) { perror("fopen"); return -1; }
    size_t total = kib * 1024;
    unsigned char *buf = malloc(total > 4096 ? 4096 : total);
    if (!buf) { fclose(f); return -1; }
    size_t off = 0;
    while (off < total) {
        size_t chunk = total - off < 4096 ? total - off : 4096;
        for (size_t i = 0; i < chunk; i++) buf[i] = (unsigned char)((off + i) & 0xff);
        fwrite(buf, 1, chunk, f);
        off += chunk;
    }
    free(buf);
    fclose(f);
    return 0;
}

/*
 * Serve the file to the client using:
 *   1) writev() for a small HTTP-style header (writeable in userspace)
 *   2) sendfile() for the bulk zero-copy file -> socket
 *
 * Returns the number of bytes transferred (header + file).
 */
static long long serve_file(int client_fd, int file_fd, size_t file_size) {
    /* header line: "X-Source: sendfile\r\nContent-Length: N\r\n\r\n" */
    char hdr[128];
    int hlen = snprintf(hdr, sizeof(hdr),
                        "X-Source: sendfile\r\nContent-Length: %zu\r\n\r\n",
                        file_size);
    struct iovec iov[1] = { { .iov_base = hdr, .iov_len = (size_t)hlen } };
    ssize_t w = writev(client_fd, iov, 1);
    if (w < 0) { perror("writev header"); return -1; }

    off_t offset = 0;
    size_t remaining = file_size;
    long long total = (long long)w;

    while (remaining > 0) {
        ssize_t s = sendfile(client_fd, file_fd, &offset, remaining);
        if (s == -1) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                /* socket buffer temporarily full; back to event loop */
                return total;
            }
            if (errno == EINTR) continue;
            perror("sendfile");
            return -1;
        }
        if (s == 0) break; /* EOF */
        remaining -= (size_t)s;
        total += s;
    }
    return total;
}

int main(int argc, char *argv[]) {
    int port = (argc > 1) ? atoi(argv[1]) : 9000;
    int kib = (argc > 2) ? atoi(argv[2]) : 1024;     /* default 1 MiB */

    if (build_demo_file((size_t)kib) != 0) return 1;

    int file_fd = open(FILE_PATH, O_RDONLY);
    if (file_fd < 0) { perror("open"); return 1; }
    struct stat st;
    fstat(file_fd, &st);
    size_t file_size = (size_t)st.st_size;

    int lfd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0);
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

    printf("sendfile serving %s (%zu bytes) on :%d\n",
           FILE_PATH, file_size, port);
    struct epoll_event events[64];
    for (;;) {
        int nev = epoll_wait(ep, events, 64, 1000);
        for (int i = 0; i < nev; i++) {
            /* accept loop */
            if (events[i].data.ptr == NULL) {
                int cfd = accept4(lfd, NULL, NULL, SOCK_NONBLOCK);
                if (cfd < 0) continue;
                ev.data.ptr = (void *)(intptr_t)(cfd | 1);
                ev.events = EPOLLIN;
                epoll_ctl(ep, EPOLL_CTL_ADD, cfd, &ev);
                continue;
            }
            int cfd = (int)((intptr_t)events[i].data.ptr & ~1);
            /* any readable data from client is the trigger; */
            /* drain, then push file content via sendfile */
            char junk[256];
            ssize_t n;
            do { n = recv(cfd, junk, sizeof junk, 0); } while (n > 0);
            if (n == 0 || (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) {
                close(cfd);
                epoll_ctl(ep, EPOLL_CTL_DEL, cfd, NULL);
                continue;
            }
            /* serve the file with sendfile */
            serve_file(cfd, file_fd, file_size);
            /* half-close + close */
            shutdown(cfd, SHUT_WR);
            close(cfd);
            epoll_ctl(ep, EPOLL_CTL_DEL, cfd, NULL);
        }
    }
    close(ep); close(lfd); close(file_fd);
    return 0;
}
