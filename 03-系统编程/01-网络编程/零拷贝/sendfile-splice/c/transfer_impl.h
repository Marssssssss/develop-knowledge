/* transfer_impl.h — 回环测试夹具 + 四种搬运路径（实现头）。
 *
 * 由 main.c 在原地 #include：文本级包含，同一个翻译单元，零链接风险。
 * 之所以拆出来，是因为 300 行上限（_docs/OPTIMIZATION.md §1.1）。
 */
/* --------------------------------------------------------------- 测试夹具 */
typedef struct {
    int listen_fd, conn_fd;
    pid_t child;
} Fixture;

/* 建立一个 loopback TCP 连接，子进程在另一端不停 recv 直到对端关闭 */
static int fixture_open(Fixture *fx) {
    struct sockaddr_in addr;
    socklen_t alen = sizeof(addr);
    int one = 1;

    fx->listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fx->listen_fd < 0) return -1;
    setsockopt(fx->listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(PORT);
    if (bind(fx->listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) return -1;
    if (listen(fx->listen_fd, 128) < 0) return -1;
    if (getsockname(fx->listen_fd, (struct sockaddr *)&addr, &alen) < 0) return -1;

    fx->child = fork();
    if (fx->child == 0) {
        /* 接收端：把数据全部读掉并丢弃，避免发送端被流控卡住 */
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        char buf[CHUNK];
        if (fd < 0) _exit(1);
        if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) _exit(2);
        for (;;) {
            ssize_t n = recv(fd, buf, sizeof(buf), 0);
            if (n <= 0) break;
        }
        close(fd);
        _exit(0);
    }
    if (fx->child < 0) return -1;
    fx->conn_fd = accept(fx->listen_fd, NULL, NULL);
    return fx->conn_fd < 0 ? -1 : 0;
}

static void fixture_close(Fixture *fx) {
    int status;
    close(fx->conn_fd);
    close(fx->listen_fd);
    waitpid(fx->child, &status, 0);
}

/* ---------------------------------------------------------------- 四种方式 */
/* 1) 传统 read + write：数据要两次穿过用户态缓冲区 */
static long long send_read_write(int out_fd, const char *path) {
    char *buf = malloc(CHUNK);
    long long total = 0;
    int in_fd = open(path, O_RDONLY);
    if (!buf || in_fd < 0) return -1;
    for (;;) {
        ssize_t n = read(in_fd, buf, CHUNK);
        ssize_t off = 0;
        if (n < 0) { if (errno == EINTR) continue; break; }
        if (n == 0) break;
        while (off < n) {                       /* 写可能短写，必须循环 */
            ssize_t w = write(out_fd, buf + off, (size_t)(n - off));
            if (w < 0) { if (errno == EINTR) continue; break; }
            off += w;
        }
        total += n;
    }
    close(in_fd);
    free(buf);
    return total;
}

/* 2) mmap + write：省掉"内核→用户"的拷贝，但仍要一次"用户→socket"拷贝 */
static long long send_mmap_write(int out_fd, const char *path) {
    struct stat st;
    long long total = 0;
    int in_fd = open(path, O_RDONLY);
    char *p;
    if (in_fd < 0 || fstat(in_fd, &st) < 0) return -1;
    p = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_SHARED, in_fd, 0);
    if (p == MAP_FAILED) { close(in_fd); return -1; }
    for (off_t off = 0; off < st.st_size;) {
        size_t n = (size_t)((st.st_size - off) > CHUNK ? CHUNK : (st.st_size - off));
        ssize_t w = write(out_fd, p + off, n);
        if (w < 0) { if (errno == EINTR) continue; break; }
        off += w;
        total += w;
    }
    munmap(p, (size_t)st.st_size);
    close(in_fd);
    return total;
}

/* 3) sendfile：数据不出内核；out_fd 是 socket，in_fd 必须支持 mmap-like 操作 */
static long long send_sendfile(int out_fd, const char *path) {
    struct stat st;
    off_t off = 0;
    long long total = 0;
    int in_fd = open(path, O_RDONLY);
    if (in_fd < 0 || fstat(in_fd, &st) < 0) return -1;
    while (off < st.st_size) {
        /* offset 非 NULL 时：函数把 off 更新到"最后一个被读字节之后"，且
         * 不修改 in_fd 自己的文件偏移 —— 正好适合循环分块发送。 */
        ssize_t n = sendfile(out_fd, in_fd, &off, CHUNK);
        if (n < 0) {
            if (errno == EINTR) continue;
            if (errno == EINVAL || errno == ENOSYS) {  /* man7 建议回退 */
                fprintf(stderr, "  sendfile 不可用 (%s)，回退 read/write\n",
                        strerror(errno));
                close(in_fd);
                return send_read_write(out_fd, path);
            }
            perror("sendfile");
            break;
        }
        if (n == 0) break;
        total += n;
    }
    close(in_fd);
    return total;
}

/* 4) splice：页引用经管道搬运，两端至少一端必须是管道；socket→socket 只此一途 */
static long long send_splice(int out_fd, const char *path) {
    int pfd[2];
    long long total = 0;
    int in_fd = open(path, O_RDONLY);
    if (in_fd < 0) return -1;
    if (pipe(pfd) < 0) { close(in_fd); return -1; }
    for (;;) {
        ssize_t n = splice(in_fd, NULL, pfd[1], NULL, CHUNK, SPLICE_F_MOVE);
        if (n < 0) { if (errno == EINTR) continue; perror("splice-in"); break; }
        if (n == 0) break;
        ssize_t done = 0;
        while (done < n) {
            ssize_t w = splice(pfd[0], NULL, out_fd, NULL, (size_t)(n - done),
                              SPLICE_F_MOVE | SPLICE_F_MORE);
            if (w < 0) { if (errno == EINTR) continue; perror("splice-out"); break; }
            done += w;
        }
        total += done;
    }
    close(pfd[0]);
    close(pfd[1]);
    close(in_fd);
    return total;
}

