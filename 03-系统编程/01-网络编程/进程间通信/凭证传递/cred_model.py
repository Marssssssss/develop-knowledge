# -*- coding: utf-8 -*-
"""Unix domain socket 凭证传递 —— 模型层

原文依据（均已实读 man7 页面 unix(7) / credentials(7) / socket(7)）：

unix(7) SO_PASSCRED
    "Enabling this socket option causes receipt of the credentials of the
     sending process in an SCM_CREDENTIALS ancillary message in each
     subsequently received message. The returned credentials are those
     specified by the sender using SCM_CREDENTIALS, or a default that
     includes the sender's PID, real user ID, and real group ID, if the
     sender did not specify SCM_CREDENTIALS ancillary data."
    "When this option is set and the socket is not yet connected, a unique
     name in the abstract namespace will be generated automatically."

unix(7) SO_PEERCRED
    "This read-only socket option returns the credentials of the peer
     process connected to this socket. The returned credentials are those
     that were in effect at the time of the call to connect(2), listen(2),
     or socketpair(2)."
    "The use of this option is possible only for connected AF_UNIX stream
     sockets and for AF_UNIX stream and datagram socket pairs created
     using socketpair(2)."

unix(7) Autobind
    "If a bind(2) call specifies addrlen as sizeof(sa_family_t), or the
     SO_PASSCRED socket option was specified for a socket that was not
     explicitly bound to an address, then the socket is autobound to an
     abstract address. The address consists of a null byte followed by
     5 bytes in the character set [0-9a-f]. Thus, there is a limit of
     2^20 autobind addresses."

unix(7) Sockets API
    "UNIX domain sockets do not support the transmission of out-of-band
     data (the MSG_OOB flag for send(2) and recv(2))."
    "The send(2) MSG_MORE flag is not supported by UNIX domain sockets."
    "The SO_SNDBUF socket option does have an effect for UNIX domain
     sockets, but the SO_RCVBUF option does not."
"""

SOL_SOCKET = 1
SO_PASSCRED = 16
SO_PEERCRED = 17
SO_RCVBUF = 8
SO_SNDBUF = 7
SCM_CREDENTIALS = 2
SCM_RIGHTS = 1
MSG_OOB = 1
MSG_MORE = 0x8000

AUTOBIND_LIMIT = 1 << 20          # 5 个 hex 字符 → 16^5
AUTOBIND_CHARS = "0123456789abcdef"


class Ucred:
    """struct ucred：pid / uid / gid（unix(7) 对 SO_PEERCRED 的描述）。"""

    __slots__ = ("pid", "uid", "gid")

    def __init__(self, pid, uid, gid):
        self.pid = pid
        self.uid = uid
        self.gid = gid

    def __eq__(self, other):
        return (self.pid, self.uid, self.gid) == (other.pid, other.uid, other.gid)

    def __hash__(self):
        return hash((self.pid, self.uid, self.gid))

    def __repr__(self):
        return "ucred(pid=%d, uid=%d, gid=%d)" % (self.pid, self.uid, self.gid)


class Proc:
    """进程：区分 real uid/gid 与 effective uid/gid（credentials(7)）。"""

    def __init__(self, pid, uid, gid, euid=None, egid=None):
        self.pid = pid
        self.uid = uid
        self.gid = gid
        self.euid = uid if euid is None else euid
        self.egid = gid if egid is None else egid

    def default_cred(self):
        """unix(7)：默认凭证取 **real** user ID / **real** group ID。"""
        return Ucred(self.pid, self.uid, self.gid)


class SockError(Exception):
    pass


class UnixSock:
    def __init__(self, kind="stream", proc=None):
        self.kind = kind                   # "stream" | "dgram"
        self.proc = proc
        self.passcred = False
        self.addr = None                   # ("path", p) | ("abstract", b)
        self.autobound = False
        self.peer = None
        self.peer_cred = None              # connect 时刻的快照
        self.inbox = []
        self.sndbuf = 2048
        self.rcvbuf = 2048                 # SO_RCVBUF 对 AF_UNIX 无效

    # ---------------------------------------------------------- 选项
    def setsockopt(self, level, optname, value):
        if level != SOL_SOCKET:
            raise SockError("level 必须是 SOL_SOCKET")
        if optname == SO_PASSCRED:
            self.passcred = bool(value)
            if self.passcred and self.addr is None and self.peer is None:
                self.autobind()
            return True
        if optname == SO_SNDBUF:
            self.sndbuf = value
            return True
        if optname == SO_RCVBUF:
            return False                   # unix(7)：对 UNIX socket 无效
        raise SockError("未知选项 %d" % optname)

    def getsockopt(self, level, optname):
        if level != SOL_SOCKET:
            raise SockError("level 必须是 SOL_SOCKET")
        if optname == SO_PEERCRED:
            if self.peer_cred is None:
                raise SockError(
                    "SO_PEERCRED 只适用于已连接的 stream socket 与 socketpair")
            return self.peer_cred
        if optname == SO_PASSCRED:
            return int(self.passcred)
        raise SockError("未知选项 %d" % optname)

    # ---------------------------------------------------------- 地址
    def bind(self, path=None, addrlen=None):
        import ctypes
        if path is None and addrlen == ctypes.sizeof(ctypes.c_ushort):
            self.autobind()
            return self.addr
        self.addr = ("path", path)
        self.autobound = False
        return self.addr

    def autobind(self, seq=0):
        """抽象地址 = 一个空字节 + 5 个 [0-9a-f] 字符。"""
        h = "".join(AUTOBIND_CHARS[(seq >> (4 * i)) & 0xF] for i in range(5))
        self.addr = ("abstract", b"\x00" + h.encode())
        self.autobound = True
        return self.addr

    # ---------------------------------------------------------- 连接
    def connect(self, other):
        """unix(7)：SO_PEERCRED 取的是 connect 时刻的凭证，之后不再更新。"""
        self.peer = other
        other.peer = self
        self.peer_cred = other.proc.default_cred() if other.proc else None
        other.peer_cred = self.proc.default_cred() if self.proc else None
        return True

    def listen(self, proc=None):
        """被动方的 peer_cred 在 listen 时刻对本端无效，这里只记录监听态。"""
        self.listening = True


def socketpair(kind="stream", a_proc=None, b_proc=None):
    """socketpair 两端在**创建时**就互相信任，peer_cred 立即可用。"""
    a = UnixSock(kind, a_proc)
    b = UnixSock(kind, b_proc)
    a.peer = b
    b.peer = a
    a.peer_cred = b_proc.default_cred() if b_proc else None
    b.peer_cred = a_proc.default_cred() if a_proc else None
    return a, b


# ---------------------------------------------------------------- 收发
def sendmsg(sock, data, cmsgs=None, flags=0):
    """unix(7)：AF_UNIX 不支持 MSG_OOB / MSG_MORE。"""
    if flags & MSG_OOB:
        raise SockError("AF_UNIX 不支持带外数据（MSG_OOB）")
    if flags & MSG_MORE:
        raise SockError("AF_UNIX 不支持 MSG_MORE")
    peer = sock.peer
    if peer is None:
        raise SockError("未连接")
    cmsgs = list(cmsgs or [])
    has_cred = any(t == SCM_CREDENTIALS for _, t, _ in cmsgs)
    if not has_cred and sock.proc is not None:
        # unix(7) 原文：未指定时填充「发送方的 PID、real UID、real GID」
        cmsgs.append((SOL_SOCKET, SCM_CREDENTIALS, sock.proc.default_cred()))
    peer.inbox.append((data, cmsgs))
    return len(data)


def recvmsg(sock, flags=0):
    """SO_PASSCRED 决定接收方**是否**看到 SCM_CREDENTIALS。"""
    if flags & MSG_OOB:
        raise SockError("AF_UNIX 不支持带外数据（MSG_OOB）")
    if not sock.inbox:
        raise SockError("无数据")
    data, cmsgs = sock.inbox.pop(0)
    if not sock.passcred:
        cmsgs = [c for c in cmsgs if c[1] != SCM_CREDENTIALS]
    return data, cmsgs


def peer_cred(sock):
    """getsockopt(SO_PEERCRED) 的便捷入口。"""
    return sock.getsockopt(SOL_SOCKET, SO_PEERCRED)
