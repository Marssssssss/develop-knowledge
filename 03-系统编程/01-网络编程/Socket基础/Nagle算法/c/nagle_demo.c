/*
 * nagle_demo.c — Nagle algorithm & TCP_NODELAY on loopback
 *
 * Demonstrates:
 *   demo 1 — baseline (Nagle on)  : 100 single-byte writes => fewer segments
 *   demo 2 — TCP_NODELAY=1        : 100 single-byte writes => 100 segments
 *   demo 3 — interactive echo     : 1-byte ping/pong latency with Nagle on
 *
 * Measurement: getsockopt(TCP_INFO) → tcpi_segs_out (Linux struct tcp_info)
 *
 * Linux only (uses TCP_INFO + struct tcp_info from <linux/tcp.h>).
 * Compile: gcc -O2 -Wall -Wextra -pthread nagle_demo.c -o /tmp/nagle_demo
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <pthread.h>
#include <time.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <linux/tcp.h>
#include <arpa/inet.h>

#define LOOPBACK "127.0.0.1"
#define PORT     0              /* ephemeral */
#define N_WRITES 100

/* Read tcpi_segs_out from TCP_INFO. Linux struct layout fixed by <linux/tcp.h> */
static int get_segs_out(int fd, uint32_t *out)
{
    struct tcp_info info;
    socklen_t len = sizeof(info);
    if (getsockopt(fd, IPPROTO_TCP, TCP_INFO, &info, &len) < 0) {
        return -1;
    }
    /* In struct tcp_info, tcpi_segs_out sits at offset defined by the kernel.
     * We compute it via &info.tcpi_segs_out so layout stays correct under
     * kernel ABI changes (struct tcp_info is upward-compatible). */
    *out = info.tcpi_segs_out;
    return 0;
}

/* Track total bytes the server received for a given run. */
static volatile long g_server_bytes = 0;

static void *server_thread(void *arg)
{
    int srv = *(int *)arg;
    int cli;
    struct sockaddr_in addr;
    socklen_t alen = sizeof(addr);
    cli = accept(srv, (struct sockaddr *)&addr, &alen);
    if (cli < 0) { perror("accept"); return NULL; }

    /* Drain until peer closes — client closes after writes. */
    char buf[256];
    long total = 0;
    for (;;) {
        ssize_t n = recv(cli, buf, sizeof(buf), 0);
        if (n <= 0) break;
        total += n;
    }
    g_server_bytes = total;
    close(cli);
    return NULL;
}

/* Bind/listen on an ephemeral port, return fd & port. */
static int start_server(uint16_t *out_port)
{
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) { perror("socket"); exit(1); }
    int yes = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));

    struct sockaddr_in a = {0};
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    a.sin_port = 0;                                /* ephemeral */
    if (bind(srv, (struct sockaddr *)&a, sizeof(a)) < 0) {
        perror("bind"); exit(1);
    }
    if (listen(srv, 1) < 0) { perror("listen"); exit(1); }

    socklen_t alen = sizeof(a);
    getsockname(srv, (struct sockaddr *)&a, &alen);
    *out_port = ntohs(a.sin_port);
    return srv;
}

/* Connect client to 127.0.0.1:port. */
static int connect_client(uint16_t port)
{
    int c = socket(AF_INET, SOCK_STREAM, 0);
    if (c < 0) { perror("socket(c)"); exit(1); }
    struct sockaddr_in a = {0};
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    a.sin_port = htons(port);
    if (connect(c, (struct sockaddr *)&a, sizeof(a)) < 0) {
        perror("connect"); exit(1);
    }
    return c;
}

/* =========================================================================
 * demo 1 & 2 — Nagle vs TCP_NODELAY: send 100 single-byte writes, count
 * the actual TCP segments produced (via TCP_INFO).
 * ========================================================================= */
static void demo_send_n_writes(int nodelay_on)
{
    uint16_t port;
    int srv = start_server(&port);
    pthread_t th;
    pthread_create(&th, NULL, server_thread, &srv);
    int c = connect_client(port);

    if (nodelay_on) {
        int v = 1;
        setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &v, sizeof(v));
    }

    /* Baseline. */
    uint32_t segs_before = 0;
    get_segs_out(c, &segs_before);

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    char b = 'a';
    for (int i = 0; i < N_WRITES; i++) {
        if (send(c, &b, 1, 0) != 1) { perror("send"); exit(1); }
        /* Tiny pause keeps the loop from running faster than the kernel
         * can coalesce — even with Nagle off we want each call to be
         * observable, not artificially merged in user space. */
        struct timespec slp = {0, 1000};          /* 1 µs */
        nanosleep(&slp, NULL);
    }
    /* Half-close so server gets EOF. */
    shutdown(c, SHUT_WR);
    clock_gettime(CLOCK_MONOTONIC, &t1);

    uint32_t segs_after = 0;
    get_segs_out(c, &segs_after);

    double us = (t1.tv_sec - t0.tv_sec) * 1e6 +
                (t1.tv_nsec - t0.tv_nsec) / 1e3;
    uint32_t sent = segs_after - segs_before;

    printf("=== %s ==============================================\n",
           nodelay_on ? "demo 2  TCP_NODELAY=1" : "demo 1  Nagle on  ");
    printf("  Sent %d single-byte writes (%d bytes total)\n", N_WRITES, N_WRITES);
    printf("  TCP segments emitted (tcpi_segs_out delta): %u\n", sent);
    printf("  wall time                         : %.1f µs (%.2f µs/write)\n",
           us, us / N_WRITES);
    printf("  server received                   : %ld bytes\n", g_server_bytes);
    if (!nodelay_on && sent < N_WRITES) {
        printf("  >>> Nagle coalesced %u bytes into %u segments "
               "(ratio %.2fx)\n",
               N_WRITES, sent, (double)N_WRITES / sent);
    }
    if (nodelay_on && sent == N_WRITES) {
        printf("  >>> TCP_NODELAY emitted one segment per write "
               "as expected\n");
    }

    close(c);
    close(srv);
    pthread_join(th, NULL);
}

/* =========================================================================
 * demo 3 — interactive echo: send 1 byte, wait for 1-byte reply.
 * Under Nagle the client holds the NEXT byte until the previous ACK arrives.
 * We send 50 ping/pong pairs with 200 µs gap on each side and count total
 * time. With TCP_NODELAY it's the floor (just the network round-trip).
 * ========================================================================= */
static volatile long g_pongs = 0;
static void *echo_server(void *arg)
{
    int cli = *(int *)arg;
    char in;
    for (;;) {
        ssize_t n = recv(cli, &in, 1, 0);
        if (n <= 0) break;
        if (send(cli, &in, 1, 0) != 1) break;
    }
    close(cli);
    return NULL;
}
static void *accept_echo(void *arg)
{
    int srv = *(int *)arg;
    int cli;
    struct sockaddr_in addr;
    socklen_t alen = sizeof(addr);
    cli = accept(srv, (struct sockaddr *)&addr, &alen);
    if (cli < 0) return NULL;
    echo_server(&cli);
    return NULL;
}

static void demo_interactive(int nodelay_on)
{
    uint16_t port;
    int srv = start_server(&port);
    pthread_t th;
    pthread_create(&th, NULL, accept_echo, &srv);
    int c = connect_client(port);

    if (nodelay_on) {
        int v = 1;
        setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &v, sizeof(v));
    }

    const int ROUNDS = 50;
    char b = 'X';
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int i = 0; i < ROUNDS; i++) {
        if (send(c, &b, 1, 0) != 1) { perror("send"); exit(1); }
        char r;
        if (recv(c, &r, 1, 0) != 1) { perror("recv"); exit(1); }
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    shutdown(c, SHUT_RDWR);
    close(c);

    double us = (t1.tv_sec - t0.tv_sec) * 1e6 +
                (t1.tv_nsec - t0.tv_nsec) / 1e3;
    printf("=== demo 3  interactive echo (%s) ========\n",
           nodelay_on ? "TCP_NODELAY=1" : "Nagle on  ");
    printf("  %d ping/pong rounds in %.1f µs (%.3f µs/round, RTT ≈ %.3f µs)\n",
           ROUNDS, us, us / ROUNDS, us / ROUNDS / 2.0);
    printf("  (On loopback both are sub-microsecond; the difference is "
           "the *algorithm's* wait, dwarfed by syscall overhead.)\n");

    close(srv);
    pthread_join(th, NULL);
}

int main(void)
{
    printf("# Nagle vs TCP_NODELAY — loopback measurement\n");
    printf("# (server & client in same process; counts via TCP_INFO)\n\n");

    demo_send_n_writes(0);                            /* demo 1 — Nagle */
    printf("\n");
    demo_send_n_writes(1);                            /* demo 2 — no delay */
    printf("\n");
    demo_interactive(0);                              /* demo 3a */
    demo_interactive(1);                              /* demo 3b */

    return 0;
}
