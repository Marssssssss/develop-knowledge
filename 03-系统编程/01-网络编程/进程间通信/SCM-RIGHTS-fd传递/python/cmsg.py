#!/usr/bin/env python3
"""cmsg 尺寸算术与常量（从 scm_model.py 拆出）。

CMSG_ALIGN / CMSG_LEN / CMSG_SPACE 这三个名字的差别是本 demo 最容易踩的坑：
CMSG_LEN 是「写进 cmsg_len 的值」（不含尾部填充），CMSG_SPACE 是「要预留的
缓冲字节数」（含填充）。1 个 fd 时两者相差 4 字节，按 CMSG_LEN 预留缓冲就会
一个 fd 都收不到。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 常量

SCM_MAX_FD = 253          # man7 unix(7)：Linux ≥ 2.6.38 为 253（更早 255）
SCM_MAX_FD_OLD = 255
CMSGHDR_SIZE = 16         # x86-64: size_t cmsg_len(8) + int level(4) + int type(4)
SOL_SOCKET = 1
SCM_RIGHTS = 0x01

# msg_flags（recvmsg 返回）
MSG_CTRUNC = 0x08
MSG_TRUNC = 0x20
# recvmsg 的入参 flag（Linux 2.6.23 起）：收到的 fd 原子地带上 FD_CLOEXEC，
# 避免 "recvmsg 之后、fcntl 之前" 那段 exec 泄漏窗口
MSG_CMSG_CLOEXEC = 0x40000000

O_CLOEXEC = 0o2000000

# 错误号（用真实 errno 名，便于与 C 版本对照）
EINVAL = "EINVAL"
EBADF = "EBADF"
ETOOMANYREFS = "ETOOMANYREFS"
EMFILE = "EMFILE"


class ScmError(Exception):
    """带 errno 名的发送/接收错误。"""

    def __init__(self, errno_name: str, why: str):
        super().__init__(f"{errno_name}: {why}")
        self.errno_name = errno_name
        self.why = why


# ---------------------------------------------------------------- cmsg 布局

def cmsg_align(length: int) -> int:
    """CMSG_ALIGN：按 sizeof(long) 向上取整（这里取 8，x86-64）。"""
    return (length + 7) & ~7


def cmsg_len(data_len: int) -> int:
    """CMSG_LEN：要写进 cmsghdr.cmsg_len 的值 —— **不含**尾部填充。"""
    return cmsghdr_size() + data_len


def cmsg_space(data_len: int) -> int:
    """CMSG_SPACE：这样一个控制项**实际占用的缓冲字节数 —— 含**尾部填充。"""
    return cmsg_align(cmsghdr_size() + data_len)


def cmsghdr_size() -> int:
    return CMSGHDR_SIZE


def fds_total_space(nfds: int) -> int:
    """传 nfds 个 fd 时，msg_control 缓冲至少要多大。"""
    return cmsg_space(4 * nfds)


def fds_fit(ctrl_buf_size: int, nfds: int) -> int:
    """在 ctrl_buf_size 字节的控制缓冲里最多能装下几个 fd。

    注意边界来自 CMSG_SPACE（含填充），不是 4*nfds 也不是 CMSG_LEN。
    """
    n = 0
    while n < nfds and fds_total_space(n + 1) <= ctrl_buf_size:
        n += 1
    return n

