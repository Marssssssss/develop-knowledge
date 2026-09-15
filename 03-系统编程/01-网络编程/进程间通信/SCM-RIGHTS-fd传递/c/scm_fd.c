/* scm_fd.c — 真实的 send_fd / recv_fd（Linux）。
 *
 * 只保留内核接口必需的东西：cmsg 尺寸算术 + 一次 sendmsg / recvmsg。
 * 复杂的场景编排在 main.c，数值断言在 ../python/main.py（本机无编译器，
 * C 代码走人工评审，所以注释里把每处"为什么这么写"都讲清楚）。
 */
#include "scm_fd.h"

#include <errno.h>
#include <string.h>

#if defined(__linux__)
#include <sys/socket.h>
#include <unistd.h>
#define SCM_CMSGHDR_SIZE ((size_t)sizeof(struct cmsghdr))
#else
/* 非 Linux 上 <sys/socket.h> 没有 cmsghdr；16 是 x86-64 Linux 的实测值
 * （size_t cmsg_len(8) + int cmsg_level(4) + int cmsg_type(4)），
 * 只为让尺寸算术函数在别处也能编译。 */
#define SCM_CMSGHDR_SIZE ((size_t)16)
#endif

size_t scm_cmsg_align(size_t len)
{
    /* CMSG_ALIGN：按 sizeof(long) 向上取整。x86-64 上 sizeof(long) == 8。 */
    const size_t align = sizeof(long);
    return (len + align - 1) & ~(align - 1);
}

size_t scm_cmsg_len(size_t datalen)
{
    /* CMSG_LEN = sizeof(struct cmsghdr) + datalen，**不含**尾部填充。
     * 注意它是"写进 cmsg_len 的值"，不是"要预留的缓冲"—— 这是个经典混淆点。 */
    return SCM_CMSGHDR_SIZE + datalen;
}

size_t scm_cmsg_space(size_t datalen)
{
    /* CMSG_SPACE = CMSG_ALIGN(sizeof(struct cmsghdr) + datalen)，**含**填充。 */
    return scm_cmsg_align(SCM_CMSGHDR_SIZE + datalen);
}

int scm_fds_fit(size_t ctrl_buf, int nfds)
{
    int n = 0;
    while (n < nfds && scm_cmsg_space((size_t)4 * (size_t)(n + 1)) <= ctrl_buf) {
        n++;
    }
    return n;
}

const char *scm_errno_name(int err)
{
    switch (err) {
    case 0:            return "0";
    case EINVAL:       return "EINVAL";
    case EBADF:        return "EBADF";
    case EMFILE:       return "EMFILE";
    case ENFILE:       return "ENFILE";
    case EMSGSIZE:     return "EMSGSIZE";
    case ENOBUFS:      return "ENOBUFS";
    case ENOTSOCK:     return "ENOTSOCK";
    case EOPNOTSUPP:   return "EOPNOTSUPP";
    default:           return "(其他 errno)";
    }
}

#if !defined(__linux__)

int scm_send_fd(int sock, int fd, const char *tag, int no_data, int *out_err)
{
    (void)sock; (void)fd; (void)tag; (void)no_data;
    *out_err = ENOSYS;
    return 0;
}

int scm_recv_fd(int sock, int *out_fd, size_t ctrl_buf_size, int flags,
                int *out_msg_flags, char *tag_buf, size_t tag_len, int *out_err)
{
    (void)sock; (void)out_fd; (void)ctrl_buf_size; (void)flags;
    (void)out_msg_flags; (void)tag_buf; (void)tag_len;
    *out_err = ENOSYS;
    return 0;
}

#else /* __linux__ */

int scm_send_fd(int sock, int fd, const char *tag, int no_data, int *out_err)
{
    char byte = '!';
    struct iovec io;
    struct msghdr msg;
    struct cmsghdr *cmsg;
    /* 控制缓冲必须包在 union 里，借它的对齐要求保证 cmsg 本身是
     * 对齐的 —— cmsg(3) 的示例就是这么写的。 */
    union {
        char buf[CMSG_SPACE(sizeof(int))];
        struct cmsghdr align;
    } u;

    memset(&u, 0, sizeof(u));       /* CMSG_NXTHDR 依赖缓冲先清零 */
    memset(&msg, 0, sizeof(msg));
    io.iov_base = no_data ? NULL : &byte;
    io.iov_len = no_data ? 0 : sizeof(byte);
    msg.msg_iov = &io;
    msg.msg_iovlen = 1;
    msg.msg_control = u.buf;
    msg.msg_controllen = sizeof(u.buf);

    cmsg = CMSG_FIRSTHDR(&msg);
    cmsg->cmsg_level = SOL_SOCKET;
    cmsg->cmsg_type = SCM_RIGHTS;
    cmsg->cmsg_len = CMSG_LEN(sizeof(int));
    /* CMSG_DATA 返回的指针不能假定对齐，标准做法是 memcpy 而不是强转赋值。 */
    memcpy(CMSG_DATA(cmsg), &fd, sizeof(fd));
    (void)tag;   /* tag 只是给"至少 1 字节真实数据"占位，这里用固定 '!' */

    if (sendmsg(sock, &msg, 0) < 0) {
        *out_err = errno;
        return 0;
    }
    *out_err = 0;
    return 1;
}

int scm_recv_fd(int sock, int *out_fd, size_t ctrl_buf_size, int flags,
                int *out_msg_flags, char *tag_buf, size_t tag_len, int *out_err)
{
    char byte = 0;
    struct iovec io;
    struct msghdr msg;
    struct cmsghdr *cmsg;
    char small[128];
    union {
        char buf[256];
        struct cmsghdr align;
    } u;
    void *ctrl;
    ssize_t n;

    if (ctrl_buf_size == 0 || ctrl_buf_size > sizeof(u.buf)) {
        ctrl_buf_size = sizeof(u.buf);
    }
    ctrl = (ctrl_buf_size <= sizeof(small)) ? (void *)small : (void *)u.buf;
    memset(ctrl, 0, ctrl_buf_size);

    memset(&msg, 0, sizeof(msg));
    io.iov_base = &byte;
    io.iov_len = sizeof(byte);
    msg.msg_iov = &io;
    msg.msg_iovlen = 1;
    msg.msg_control = ctrl;
    msg.msg_controllen = ctrl_buf_size;

    n = recvmsg(sock, &msg, flags);
    if (n < 0) {
        *out_err = errno;
        return 0;
    }
    *out_msg_flags = msg.msg_flags;      /* MSG_CTRUNC 在这里 */
    *out_fd = -1;
    if (n == 1 && tag_buf && tag_len > 0) {
        tag_buf[0] = byte;
        if (tag_len > 1) {
            tag_buf[1] = '\0';
        }
    }
    for (cmsg = CMSG_FIRSTHDR(&msg); cmsg != NULL; cmsg = CMSG_NXTHDR(&msg, cmsg)) {
        if (cmsg->cmsg_level == SOL_SOCKET && cmsg->cmsg_type == SCM_RIGHTS) {
            if (cmsg->cmsg_len >= CMSG_LEN(sizeof(int))) {
                memcpy(out_fd, CMSG_DATA(cmsg), sizeof(int));
            }
        }
    }
    *out_err = 0;
    return 1;
}

#endif /* __linux__ */
