/* scm_fd.h — SCM_RIGHTS fd 传递的辅助封装（Linux）。
 *
 * 这里只做两件事：cmsg 缓冲的尺寸算术，以及一对真实的 send_fd / recv_fd。
 * 场景编排与断言在 main.c。数值断言的可跑版本在 ../python/main.py。
 */
#ifndef SCM_FD_H
#define SCM_FD_H

#include <stddef.h>

/* man7 unix(7)：SCM_MAX_FD 在 Linux >= 2.6.38 为 253，更早为 255。
 * 超出时 sendmsg(2) 失败并置 EINVAL。 */
#define SCM_MAX_FD_LINUX 253
#define SCM_MAX_FD_OLD   255

/* 控制缓冲尺寸算术：与 CMSG_ALIGN / CMSG_LEN / CMSG_SPACE 等价 */
size_t scm_cmsg_align(size_t len);
size_t scm_cmsg_len(size_t datalen);    /* 写进 cmsg_len 的值，不含尾部填充 */
size_t scm_cmsg_space(size_t datalen);  /* 实际占用的缓冲字节数，含尾部填充 */

/* 在 ctrl_buf 字节的控制缓冲里最多装得下几个 fd（依据 CMSG_SPACE）。 */
int scm_fds_fit(size_t ctrl_buf, int nfds);

/* errno 名，便于断言与打印。 */
const char *scm_errno_name(int err);

/* 发送一个 fd（附带 tag 作为那"至少 1 字节真实数据"）。
 * 成功返回 1；失败返回 0 并把 errno 写入 *out_err。
 * no_data != 0 时故意不带真实数据，用来验证流式 socket 上的 EINVAL。 */
int scm_send_fd(int sock, int fd, const char *tag, int no_data, int *out_err);

/* 接收一个 fd。ctrl_buf_size 用来验证"缓冲过小 → MSG_CTRUNC + 多余 fd 被关闭"。
 * 成功返回 1；*out_fd 为收到的 fd（<0 表示没收到）；
 * *out_msg_flags 带回 MSG_CTRUNC 等；tag 内容写入 tag_buf。 */
int scm_recv_fd(int sock, int *out_fd, size_t ctrl_buf_size, int flags,
                int *out_msg_flags, char *tag_buf, size_t tag_len, int *out_err);

#endif /* SCM_FD_H */
