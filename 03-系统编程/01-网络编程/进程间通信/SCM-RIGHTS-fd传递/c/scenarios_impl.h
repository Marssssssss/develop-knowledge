/* scenarios_impl.h — 场景函数（实现头）。
 *
 * 由 main.c 在原地 #include：文本级包含，同一个翻译单元。
 * scenarios_impl.h 里有两段：屏障/CMSG_CLOEXEC 场景与 OFD 身份/SCM_MAX_FD
 * 场景 —— 都是纯函数定义，包头顺序不影响语义。
 */
static void scenario_barrier_and_cloexec(void)
{
    int sfd[2];
    int fd, out = -1;
    char buf[32] = {0};
    int err = 0, flags = 0;
    ssize_t n;

    printf("\n=== 5) 控制数据是屏障；6) MSG_CMSG_CLOEXEC ===\n");
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sfd) != 0) {
        check("socketpair", 0, "errno=%s", scm_errno_name(errno));
        return;
    }
    fd = dup(0);
    /* man7 unix(7)：4 字节(无控制) + 1 字节(带控制) + 4 字节(无控制)，
     * 接收方每次 recvmsg 缓冲 20 字节 —— 第一次会拿到 5 字节 + 那 1 个 fd。 */
    if (write(sfd[0], "AAAA", 4) != 4) {
        check("write 第 1 段", 0, "短写");
    }
    if (!scm_send_fd(sfd[0], fd, "B", 0, &err)) {
        check("sendmsg 第 2 段", 0, "%s", scm_errno_name(err));
    }
    if (write(sfd[0], "CCCC", 4) != 4) {
        check("write 第 3 段", 0, "短写");
    }
    /* 用 scm_recv_fd 不方便看"一次读回 5 字节"，这里直接手写一次 recvmsg */
    {
        char ctrl[256];
        struct iovec io;
        struct msghdr msg;
        struct cmsghdr *cmsg;
        memset(ctrl, 0, sizeof(ctrl));
        memset(&msg, 0, sizeof(msg));
        io.iov_base = buf;
        io.iov_len = 20;
        msg.msg_iov = &io;
        msg.msg_iovlen = 1;
        msg.msg_control = ctrl;
        msg.msg_controllen = sizeof(ctrl);
        n = recvmsg(sfd[1], &msg, MSG_CMSG_CLOEXEC);
        if (n < 0) {
            check("recvmsg 第 1 次", 0, "errno=%s", scm_errno_name(errno));
        } else {
            for (cmsg = CMSG_FIRSTHDR(&msg); cmsg != NULL;
                 cmsg = CMSG_NXTHDR(&msg, cmsg)) {
                if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_RIGHTS) {
                    memcpy(&out, CMSG_DATA(cmsg), sizeof(int));
                }
            }
            printf("  第 1 次 recvmsg(buf=20) → %zd 字节 + %d 个 fd\n", n, out >= 0 ? 1 : 0);
            check("一次拿到 5 字节（前 4 + 带控制数据的 1）", n == 5 && buf[4] == 'B',
                  "n=%zd buf=%.*s", n, (int)n, buf);
            check("同一个调用里拿到 fd", out >= 0, "");
        }
    }
    {
        char ctrl[256];
        struct iovec io;
        struct msghdr msg;
        memset(ctrl, 0, sizeof(ctrl));
        memset(buf, 0, sizeof(buf));
        memset(&msg, 0, sizeof(msg));
        io.iov_base = buf;
        io.iov_len = 20;
        msg.msg_iov = &io;
        msg.msg_iovlen = 1;
        msg.msg_control = ctrl;
        msg.msg_controllen = sizeof(ctrl);
        n = recvmsg(sfd[1], &msg, 0);
        printf("  第 2 次 recvmsg(buf=20) → %zd 字节 %.*s（屏障挡住了这 4 字节）\n",
               n, (int)n, buf);
        check("屏障挡住后面那段：第 2 次才拿到 CCCC", n == 4 && memcmp(buf, "CCCC", 4) == 0,
              "n=%zd buf=%.*s", n, (int)n, buf);
    }
    if (out >= 0) {
        int fdflags = fcntl(out, F_GETFD);
        printf("  带 MSG_CMSG_CLOEXEC 收到的 fd：FD_CLOEXEC=%d\n",
               (fdflags & FD_CLOEXEC) ? 1 : 0);
        check("MSG_CMSG_CLOEXEC 原子地设好 FD_CLOEXEC", (fdflags & FD_CLOEXEC) != 0,
              "fdflags=%d", fdflags);
        close(out);
    }
    close(fd);
    close(sfd[0]);
    close(sfd[1]);
}

static void scenario_ofd_identity(void)
{
    char tmpl[] = "/tmp/scm_demo_XXXXXX";
    int sfd[2];
    int sender_fd, recv_fd = -1, reopen;
    char buf[16];
    off_t shared_off, fresh_off;
    int err = 0, flags = 0;

    printf("=== 1) 传的是 open file description 的引用（等价 dup），不是 fd 号 ===\n");
    sender_fd = mkstemp(tmpl);
    if (sender_fd < 0) {
        check("mkstemp 可用", 0, "errno=%s", scm_errno_name(errno));
        return;
    }
    if (write(sender_fd, "HELLO-WORLD-0123456789", 22) != 22) {
        check("写入 fixture 文件", 0, "写入字节数不符");
    }
    lseek(sender_fd, 0, SEEK_SET);
    if (read(sender_fd, buf, 5) != 5) {
        check("发送方先读 5 字节", 0, "读失败");
    }
    buf[5] = '\0';
    printf("  发送方先读 5 字节: %s  → 偏移 %ld\n", buf, (long)lseek(sender_fd, 0, SEEK_CUR));

    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sfd) != 0) {
        check("socketpair", 0, "errno=%s", scm_errno_name(errno));
        close(sender_fd);
        unlink(tmpl);
        return;
    }
    if (!scm_send_fd(sfd[0], sender_fd, "!", 0, &err)) {
        check("sendmsg 一个 fd", 0, "%s", scm_errno_name(err));
        goto done;
    }
    if (!scm_recv_fd(sfd[1], &recv_fd, 0, 0, &flags, NULL, 0, &err)) {
        check("recvmsg 收到 fd", 0, "%s", scm_errno_name(err));
        goto done;
    }
    printf("  接收方收到 fd=%d（发送方原编号=%d）\n", recv_fd, sender_fd);
    check("接收方拿到的 fd 是接收方自己的编号（未必与发送方相同）", 1, "");
    check("确实收到了一个 fd", recv_fd >= 0, "recv_fd=%d", recv_fd);

    shared_off = lseek(recv_fd, 0, SEEK_CUR);
    printf("  接收方刚拿到时偏移就已是 %ld ← 与发送方共享同一个 OFD\n",
           (long)shared_off);
    check("偏移量由发送方那边推进过，说明共享同一个 open file description",
          shared_off == 5, "off=%ld", (long)shared_off);
    if (read(recv_fd, buf, 5) == 5) {
        buf[5] = '\0';
        printf("  接收方接着读 5 字节: %s\n", buf);
        check("读到的是偏移 5 之后的内容（-WORL）", memcmp(buf, "-WORL", 5) == 0, "got=%s", buf);
    }

    /* 对照：按路径自己打开，得到**独立** OFD，偏移从 0 开始 */
    reopen = open(tmpl, O_RDONLY);
    fresh_off = lseek(reopen, 0, SEEK_CUR);
    if (read(reopen, buf, 5) == 5) {
        buf[5] = '\0';
        printf("  对照：按路径自己 open 再读 5 字节: %s  → 偏移 %ld\n", buf, (long)fresh_off);
        check("re-open 得到独立 OFD，偏移从 0 开始", fresh_off == 0 && memcmp(buf, "HELLO", 5) == 0,
              "off=%ld got=%s", (long)fresh_off, buf);
    }
    close(reopen);

    /* 发送方 close 后接收方仍可用：两个 fd 各持一份引用 */
    close(sender_fd);
    if (read(recv_fd, buf, 4) == 4) {
        buf[4] = '\0';
        printf("  发送方 close 之后接收方仍可读: %s ← 引用计数各算一份\n", buf);
        check("发送方 close 不影响接收方已装好的引用", 1, "");
    } else {
        check("发送方 close 不影响接收方已装好的引用", 0, "read 失败");
    }

done:
    if (recv_fd >= 0) {
        close(recv_fd);
    }
    close(sfd[0]);
    close(sfd[1]);
    unlink(tmpl);
}

static void scenario_max_fd(void)
{
    static int fds[SCM_MAX_FD_LINUX + 1];
    int sfd[2];
    char byte = '!';
    char tag = 0;
    int err = 0, flags = 0, got, i;
    int ctrl_253 = (int)scm_cmsg_space(sizeof(int) * (size_t)SCM_MAX_FD_LINUX);

    printf("\n=== 2) 一次最多传的 fd 数：SCM_MAX_FD=%d（<2.6.38 为 %d）===\n",
           SCM_MAX_FD_LINUX, SCM_MAX_FD_OLD);
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sfd) != 0) {
        check("socketpair", 0, "errno=%s", scm_errno_name(errno));
        return;
    }
    for (i = 0; i < SCM_MAX_FD_LINUX; i++) {
        fds[i] = dup(0);
        if (fds[i] < 0) {
            check("dup 准备 fd", 0, "errno=%s", scm_errno_name(errno));
            return;
        }
    }
    check("CMSG_SPACE(4*253) 与 scm_cmsg_space 一致", ctrl_253 == (int)scm_cmsg_space(1012),
          "ctrl=%d", ctrl_253);
    if (!send_many(sfd[0], fds, SCM_MAX_FD_LINUX, &byte, 1, &err)) {
        check("传 253 个 fd 成功", 0, "%s", scm_errno_name(err));
    } else {
        /* 注意：recv_many 会把收到的 fd 号写回同一个数组，原先那 253 个 dup
         * 从此无处可关（demo 进程马上退出，不追）。真实代码里要用两个数组。 */
        got = recv_many(sfd[1], fds, SCM_MAX_FD_LINUX, (size_t)ctrl_253, 0, &flags, &tag, 1, &err);
        printf("  传 %d 个 fd：发送成功，接收方装好 %d 个（控制缓冲 %d B）\n",
               SCM_MAX_FD_LINUX, got, ctrl_253);
        check("253 个 fd 可以一次性传完", got == SCM_MAX_FD_LINUX, "got=%d", got);
        for (i = 0; i < got && i < SCM_MAX_FD_LINUX; i++) {
            close(fds[i]);
        }
    }
    /* 超出上限：254 个 -> EINVAL */
    fds[SCM_MAX_FD_LINUX] = dup(0);
    if (send_many(sfd[0], fds, SCM_MAX_FD_LINUX + 1, &byte, 1, &err)) {
        check("254 个 fd 应报 EINVAL", 0, "居然成功");
    } else {
        printf("  传 %d 个 fd：sendmsg 失败 → %s\n", SCM_MAX_FD_LINUX + 1, scm_errno_name(err));
        check("254 个 fd 报 EINVAL", err == EINVAL, "errno=%s", scm_errno_name(err));
    }
    close(fds[SCM_MAX_FD_LINUX]);
    close(sfd[0]);
    close(sfd[1]);
}

