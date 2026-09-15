/* main.c — SCM_RIGHTS / fd 传递 demo（Linux）。
 *
 * 本机没有 C 工具链（`which gcc cc clang` 皆无），所以这份代码是**供人工评审**
 * 的；README 里所有数值断言的可跑版本在 ../python/main.py。
 * 编译： cc -std=c11 -Wall -Wextra -pedantic -O2 -o scm_demo main.c scm_fd.c
 *
 * 覆盖点（对应 man7 unix(7) / cmsg(3) / recvmsg(2) 的原文）：
 *   1 传的是 open file description 的引用，不是 fd 号（偏移量共享）
 *   2 SCM_MAX_FD = 253，超出 EINVAL
 *   3 流式 socket 传控制数据必须夹带 >= 1 字节真实数据
 *   4 CMSG_SPACE 与 CMSG_LEN 差一个对齐填充；缓冲过小 -> MSG_CTRUNC
 *   5 控制数据是屏障（man7 的 4 / 1 / 4 字节例子）
 *   6 MSG_CMSG_CLOEXEC 原子地设好 FD_CLOEXEC
 *   7 发送无效 fd -> EBADF
 */
#include "scm_fd.h"

#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(__linux__)
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#endif

static int g_ok = 0;
static int g_fail = 0;

static void check(const char *name, int cond, const char *fmt, ...)
{
    va_list ap;
    if (cond) {
        g_ok++;
        printf("  [ok]   %s\n", name);
        return;
    }
    g_fail++;
    printf("  [FAIL] %s  ", name);
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n");
}

#if !defined(__linux__)

int main(void)
{
    printf("本 demo 依赖 AF_UNIX + SCM_RIGHTS，仅 Linux 可跑。\n");
    printf("请改跑 ../python/main.py（同一套断言的纯模型版）。\n");
    return 0;
}

#else /* __linux__ */

/* 一次 sendmsg 送 n 个 fd。缓冲静态分配，够装 SCM_MAX_FD 个。 */
static int send_many(int sock, const int *fds, int n, const char *data,
                     size_t datalen, int *out_err)
{
    static char ctrl[CMSG_SPACE(sizeof(int) * SCM_MAX_FD_LINUX) + 16];
    struct iovec io;
    struct msghdr msg;
    struct cmsghdr *cmsg;

    memset(ctrl, 0, sizeof(ctrl));      /* CMSG_NXTHDR 要求先清零 */
    memset(&msg, 0, sizeof(msg));
    io.iov_base = (void *)data;
    io.iov_len = datalen;
    msg.msg_iov = &io;
    msg.msg_iovlen = 1;
    msg.msg_control = ctrl;
    msg.msg_controllen = CMSG_SPACE(sizeof(int) * (size_t)n);
    cmsg = CMSG_FIRSTHDR(&msg);
    cmsg->cmsg_level = SOL_SOCKET;
    cmsg->cmsg_type = SCM_RIGHTS;
    cmsg->cmsg_len = CMSG_LEN(sizeof(int) * (size_t)n);
    memcpy(CMSG_DATA(cmsg), fds, sizeof(int) * (size_t)n);
    if (sendmsg(sock, &msg, 0) < 0) {
        *out_err = errno;
        return 0;
    }
    *out_err = 0;
    return 1;
}

/* 一次 recvmsg 收最多 cap 个 fd，返回实际收到的个数（-1 表示出错）。 */
static int recv_many(int sock, int *out, int cap, size_t ctrl_buf_size, int flags,
                     int *out_msg_flags, char *data, size_t datalen, int *out_err)
{
    static char ctrl[4096];
    struct iovec io;
    struct msghdr msg;
    struct cmsghdr *cmsg;
    ssize_t n;
    int got = 0;

    if (ctrl_buf_size == 0 || ctrl_buf_size > sizeof(ctrl)) {
        ctrl_buf_size = sizeof(ctrl);
    }
    memset(ctrl, 0, ctrl_buf_size);
    memset(&msg, 0, sizeof(msg));
    io.iov_base = data;
    io.iov_len = datalen;
    msg.msg_iov = &io;
    msg.msg_iovlen = 1;
    msg.msg_control = ctrl;
    msg.msg_controllen = ctrl_buf_size;
    n = recvmsg(sock, &msg, flags);
    if (n < 0) {
        *out_err = errno;
        return -1;
    }
    *out_msg_flags = msg.msg_flags;
    for (cmsg = CMSG_FIRSTHDR(&msg); cmsg != NULL; cmsg = CMSG_NXTHDR(&msg, cmsg)) {
        if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_RIGHTS) {
            int cnt = (int)((cmsg->cmsg_len - CMSG_LEN(0)) / sizeof(int));
            if (cnt > cap) {
                cnt = cap;
            }
            memcpy(out, CMSG_DATA(cmsg), sizeof(int) * (size_t)cnt);
            got = cnt;
        }
    }
    *out_err = 0;
    return got;
}

#include "scenarios_impl.h"
static void scenario_cmsg_math(void)
{
    int sfd[2];
    int three[3];
    int out[8];
    char byte = '!';
    int err = 0, flags = 0, got, i;
    int buf20 = (int)scm_cmsg_len(4);          /* 20：常常被误当成"够用" */
    int sp24 = (int)scm_cmsg_space(4);         /* 24：真正需要的 */

    printf("\n=== 4) CMSG_SPACE vs CMSG_LEN；控制缓冲过小 → MSG_CTRUNC ===\n");
    printf("  1 个 fd: CMSG_LEN(4)=%zu（写进 cmsg_len）"
           "，CMSG_SPACE(4)=%zu（要预留的缓冲）\n",
           scm_cmsg_len(4), scm_cmsg_space(4));
    check("CMSG_SPACE 比 CMSG_LEN 多一个对齐填充", sp24 - buf20 == 4,
          "%d vs %d", sp24, buf20);
    printf("  scm_fds_fit(4)=%d  scm_fds_fit(%d)=%d  scm_fds_fit(%d)=%d\n",
           scm_fds_fit(4, 1), buf20, scm_fds_fit((size_t)buf20, 1), sp24,
           scm_fds_fit((size_t)sp24, 1));
    check("按 sizeof(int)=4 预留 → 一个都收不到", scm_fds_fit(4, 1) == 0, "");
    check("按 CMSG_LEN=20 预留 → 仍然收不到", scm_fds_fit((size_t)buf20, 1) == 0, "");
    check("按 CMSG_SPACE=24 预留 → 正好收到 1 个", scm_fds_fit((size_t)sp24, 1) == 1, "");
    printf("  ⚠ 对齐粒度：CMSG_SPACE(4)=%zu 与 CMSG_SPACE(8)=%zu 相等 → "
           "1 个和 2 个 fd 占的缓冲一样大\n", scm_cmsg_space(4), scm_cmsg_space(8));
    check("按 CMSG_SPACE(4*2)=24 预留其实能装 2 个", scm_fds_fit((size_t)sp24, 2) == 2, "");

    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sfd) != 0) {
        check("socketpair", 0, "errno=%s", scm_errno_name(errno));
        return;
    }
    for (i = 0; i < 3; i++) {
        three[i] = dup(0);
    }
    if (!send_many(sfd[0], three, 3, &byte, 1, &err)) {
        check("发送 3 个 fd", 0, "%s", scm_errno_name(err));
    } else {
        got = recv_many(sfd[1], out, 8, (size_t)sp24, 0, &flags, &byte, 1, &err);
        printf("  发送 3 个 fd、控制缓冲只给 %d B → 收到 %d 个，MSG_CTRUNC=%d\n",
               sp24, got, (flags & MSG_CTRUNC) ? 1 : 0);
        check("多余 fd 触发 MSG_CTRUNC", (flags & MSG_CTRUNC) != 0, "flags=%d", flags);
        check("只装下 2 个 fd", got == 2, "got=%d", got);
        printf("  ← 被截断的那个 fd 由**内核在接收进程里自动关闭**"
               "（不是退回发送方，发送方那份仍在）\n");
        for (i = 0; i < got; i++) {
            close(out[i]);
        }
    }
    for (i = 0; i < 3; i++) {
        close(three[i]);
    }
    close(sfd[0]);
    close(sfd[1]);
}

static void scenario_errors(void)
{
    int sfd[2];
    char byte = '!';
    int err = 0;

    printf("\n=== 3) 流式 socket 必须夹带 >=1 字节真实数据；7) 无效 fd → EBADF ===\n");
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sfd) != 0) {
        check("socketpair", 0, "errno=%s", scm_errno_name(errno));
        return;
    }
    if (scm_send_fd(sfd[0], 0, "", 1, &err)) {
        check("流式 socket 传 0 字节应报 EINVAL", 0, "居然成功");
    } else {
        printf("  流式 socket sendmsg(data=0 B, fd) → %s\n", scm_errno_name(err));
        check("流式 socket 传 0 字节报 EINVAL", err == EINVAL, "errno=%s", scm_errno_name(err));
    }
    if (send_many(sfd[0], (const int[]){99999}, 1, &byte, 1, &err)) {
        check("发送无效 fd 应报 EBADF", 0, "居然成功");
    } else {
        printf("  sendmsg(fd=99999，本进程没有) → %s\n", scm_errno_name(err));
        check("发送无效 fd 报 EBADF", err == EBADF, "errno=%s", scm_errno_name(err));
    }
    close(sfd[0]);
    close(sfd[1]);
}

int main(void)
{
    printf("SCM_RIGHTS / fd 传递 demo —— Linux 真实系统调用路径\n\n");
    scenario_ofd_identity();
    scenario_max_fd();
    scenario_cmsg_math();
    scenario_barrier_and_cloexec();
    scenario_errors();
    printf("\n===== 断言结果: %d/%d 通过，%d 失败 =====\n",
           g_ok, g_ok + g_fail, g_fail);
    return g_fail ? 1 : 0;
}

#endif /* __linux__ */
