/* scatter_impl.h — TCP_INFO 统计 + 四种写策略 + readv 语义验证（实现头）。
 *
 * 由 main.c 在原地 #include：文本级包含，同一个翻译单元，零链接风险。
 */
/* -------------------------------------------------- 内核侧统计量 */
/* 内核统计的已发送 TCP 段数（Linux 4.6+ 的 tcpi_segs_out）。
 * 用它度量"包数"，比在应用层数 write 次数可靠得多。 */
static long segs_out(int fd) {
    struct tcp_info info;
    socklen_t len = sizeof(info);
    memset(&info, 0, sizeof(info));
    if (getsockopt(fd, IPPROTO_TCP, TCP_INFO, &info, &len) != 0) return -1;
    return (long)info.tcpi_segs_out;
}

static void set_opt(int fd, int level, int opt, int val) {
    if (setsockopt(fd, level, opt, &val, sizeof(val)) != 0) {
        fprintf(stderr, "  setsockopt(%d) 失败: %s\n", opt, strerror(errno));
    }
}

/* -------------------------------------------------- 四种写策略 */
typedef enum { MODE_TWO_WRITE, MODE_MERGE, MODE_WRITEV, MODE_CORK } mode_t;

static const char *mode_name(mode_t m) {
    switch (m) {
    case MODE_TWO_WRITE: return "write(header) + write(body)";
    case MODE_MERGE:     return "memcpy 合并 + write(整体)";
    case MODE_WRITEV:    return "writev(header, body)";
    case MODE_CORK:      return "TCP_CORK: write+write+uncork";
    }
    return "?";
}

static int mode_syscalls(mode_t m) {
    switch (m) {
    case MODE_TWO_WRITE: return 2;
    case MODE_MERGE:     return 1;
    case MODE_WRITEV:    return 1;
    case MODE_CORK:      return 3;  /* 2 次 write + 1 次 setsockopt */
    }
    return 0;
}

/* 把 header + body 用指定策略发出去，返回实际写出的字节数 */
static long send_with_mode(int fd, mode_t mode, int header_len, int body_len) {
    char *hdr = malloc((size_t)header_len);
    char *body = malloc((size_t)body_len);
    char *merged = NULL;
    long written = 0;

    memset(hdr, 'H', (size_t)header_len);
    memset(body, 'B', (size_t)body_len);

    switch (mode) {
    case MODE_TWO_WRITE:
        written += write(fd, hdr, (size_t)header_len);
        written += write(fd, body, (size_t)body_len);
        break;

    case MODE_MERGE:
        merged = malloc((size_t)(header_len + body_len));
        memcpy(merged, hdr, (size_t)header_len);                 /* 这一次
            memcpy 就是 writev 想省掉的东西 */
        memcpy(merged + header_len, body, (size_t)body_len);
        written += write(fd, merged, (size_t)(header_len + body_len));
        free(merged);
        break;

    case MODE_WRITEV: {
        struct iovec iov[2];
        iov[0].iov_base = hdr;
        iov[0].iov_len = (size_t)header_len;
        iov[1].iov_base = body;
        iov[1].iov_len = (size_t)body_len;
        /* man7 readv(2)：writev 写出的数据是**单一数据块**（原子），
         * 不会与其他进程的写输出交错。可能短写，必须循环。 */
        ssize_t n = writev(fd, iov, 2);
        written += n;
        break;
    }

    case MODE_CORK: {
        /* man7 sendfile(2) NOTES 推荐：先 cork，再写头，再 sendfile 正文，
         * 最后 uncork —— 把两个包压成一个。这里用 write 代替 sendfile。 */
        set_opt(fd, IPPROTO_TCP, TCP_CORK, 1);
        written += write(fd, hdr, (size_t)header_len);
        written += write(fd, body, (size_t)body_len);
        set_opt(fd, IPPROTO_TCP, TCP_CORK, 0);   /* uncork → 一齐发出 */
        break;
    }
    }
    free(hdr);
    free(body);
    return written;
}

/* 跑一个策略并返回 (包数, 实际写出的字节数) */
static void run_mode(mode_t mode, int header_len, int body_len,
                     long *segs, long *written) {
    Fixture fx;
    long before, after;
    long total;

    if (fixture_open(&fx) < 0) { perror("fixture_open"); *segs = -1; *written = -1; return; }
    /* 关掉 Nagle，避免小包被延迟合并干扰"包数"这个观测量 */
    set_opt(fx.conn_fd, IPPROTO_TCP, TCP_NODELAY, 1);

    before = segs_out(fx.conn_fd);
    total = send_with_mode(fx.conn_fd, mode, header_len, body_len);
    shutdown(fx.conn_fd, SHUT_WR);   /* 让接收端读到 EOF 后退出 */
    after = segs_out(fx.conn_fd);
    fixture_close(&fx);

    *segs = (before >= 0 && after >= 0) ? after - before : -1;
    *written = total;
}

/* ------------------------------------------------ readv 语义验证 */
/* 用 socketpair 验证"按 iov 数组顺序填满"的分散读语义。
 * 用 socketpair 而不是 loopback TCP，是为了让 write/readv 在同一个进程里可控，
 * 不必依赖子进程的接收时序。 */
static int readv_scatter_check(void) {
    struct iovec iov[3];
    char b0[100], b1[900], b2[1000];
    char payload[2000];
    int sv[2];
    ssize_t n;

    memset(payload, 'A', 1000);
    memset(payload + 1000, 'B', 1000);

    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0) { perror("socketpair"); return -1; }
    if (write(sv[0], payload, sizeof(payload)) != (ssize_t)sizeof(payload)) {
        perror("write payload");
    }
    iov[0].iov_base = b0; iov[0].iov_len = sizeof(b0);
    iov[1].iov_base = b1; iov[1].iov_len = sizeof(b1);
    iov[2].iov_base = b2; iov[2].iov_len = sizeof(b2);

    n = readv(sv[1], iov, 3);
    /* man7 readv(2): "readv() completely fills iov[0] before proceeding to
     * iov[1], and so on." —— 缓冲区按数组顺序被依次填满。 */
    CHECK(memcmp(b0, payload, sizeof(b0)) == 0, "iov[0] 应收到流的前 100 字节");
    CHECK(memcmp(b1, payload + sizeof(b0), sizeof(b1)) == 0, "iov[1] 应接着收 900 字节");
    CHECK(memcmp(b2, payload + sizeof(b0) + sizeof(b1), sizeof(b2)) == 0,
          "iov[2] 应接着收 1000 字节");
    CHECK(n == (ssize_t)sizeof(payload), "readv 应读满 2000 字节");
    close(sv[0]);
    close(sv[1]);
    return (int)n;
}

