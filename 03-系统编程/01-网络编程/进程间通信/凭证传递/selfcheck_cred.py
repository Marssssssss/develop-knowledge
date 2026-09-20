# -*- coding: utf-8 -*-
"""Unix socket 凭证传递 —— 自检

负向判据优先：
  * 「没开 SO_PASSCRED 就看不到凭证」必须成立
  * 「未连接的 datagram socket 取 SO_PEERCRED」必须失败
  * 「connect 后改 uid，SO_PEERCRED 会跟着变」必须**不成立**（快照语义）
"""
from cred_model import *  # noqa: F401,F403
import ctypes

N = 0
FAILED = []


def check(label, cond, detail=""):
    global N
    N += 1
    if cond:
        print("ok   %-56s %s" % (label, detail))
    else:
        FAILED.append(label)
        print("FAIL %-56s %s" % (label, detail))


print("== 1. SO_PASSCRED 是开关：关着就看不到凭证 ==")
client = Proc(pid=1001, uid=1000, gid=1000)
server = Proc(pid=900, uid=0, gid=0)
a, b = socketpair("stream", client, server)
sendmsg(a, b"hello")
data, cmsgs = recvmsg(b)
check("未开 SO_PASSCRED 时 ancillary 里没有 SCM_CREDENTIALS",
      not any(t == SCM_CREDENTIALS for _, t, _ in cmsgs), str(cmsgs))
b.setsockopt(SOL_SOCKET, SO_PASSCRED, 1)
sendmsg(a, b"world")
data2, cmsgs2 = recvmsg(b)
cred_msgs = [c for c in cmsgs2 if c[1] == SCM_CREDENTIALS]
check("开启后同一条连接立刻带上 SCM_CREDENTIALS", len(cred_msgs) == 1, str(cmsgs2))
check("数据是逐条独立携带的（第二条才有）",
      data2 == b"world" and len(cred_msgs) == 1)

print("\n== 2. 默认凭证 = 发送方 PID + real UID + real GID ==")
c = cred_msgs[0][2]
check("pid 是发送方的", c.pid == 1001, str(c))
check("uid 是发送方的 real uid", c.uid == 1000, str(c))
check("gid 是发送方的 real gid", c.gid == 1000, str(c))
spoof = Proc(pid=1001, uid=1000, gid=1000, euid=0, egid=0)
check("负向：默认取 real uid 而不是 effective uid",
      spoof.default_cred().uid == 1000 and spoof.euid == 0,
      "real=%d effective=%d" % (spoof.uid, spoof.euid))

print("\n== 3. 发送方显式携带 SCM_CREDENTIALS 时按发送方给的走 ==")
a3, b3 = socketpair("stream", client, server)
b3.setsockopt(SOL_SOCKET, SO_PASSCRED, 1)
stated = Ucred(pid=4242, uid=7, gid=8)
sendmsg(a3, b"x", cmsgs=[(SOL_SOCKET, SCM_CREDENTIALS, stated)])
_, cmsgs3 = recvmsg(b3)
got = [c[2] for c in cmsgs3 if c[1] == SCM_CREDENTIALS]
check("显式指定的凭证被原样投递", got and got[0] == stated, str(got))
check("不再补默认凭证（不会同时出现两份）", len(got) == 1, "n=%d" % len(got))

print("\n== 4. SCM_RIGHTS 与 SCM_CREDENTIALS 可以共存 ==")
a4, b4 = socketpair("stream", client, server)
b4.setsockopt(SOL_SOCKET, SO_PASSCRED, 1)
sendmsg(a4, b"fd", cmsgs=[(SOL_SOCKET, SCM_RIGHTS, [11, 12])])
_, cmsgs4 = recvmsg(b4)
types = sorted(t for _, t, _ in cmsgs4)
check("同一条消息里同时有 SCM_RIGHTS 与 SCM_CREDENTIALS",
      types == sorted([SCM_RIGHTS, SCM_CREDENTIALS]), str(types))

print("\n== 5. SO_PEERCRED 是 connect 时刻的快照 ==")
p1 = Proc(pid=500, uid=1000, gid=1000)
p2 = Proc(pid=600, uid=1000, gid=1000)
c1, s1 = UnixSock("stream", p1), UnixSock("stream", p2)
c1.connect(s1)
snap = peer_cred(c1)
check("拿到对端凭证", snap == Ucred(600, 1000, 1000), str(snap))
check("是快照而不是实时查询：改 p2 的 uid 不影响已取到的值",
      (setattr(p2, "uid", 0), peer_cred(c1))[1] == snap, str(peer_cred(c1)))
check("负向：快照不会因为 setuid 而更新",
      peer_cred(c1).uid == 1000 and p2.uid == 0,
      "snap.uid=%d proc.uid=%d" % (peer_cred(c1).uid, p2.uid))

print("\n== 6. SO_PEERCRED 的适用范围 ==")
# 上一节把 p2.uid 改成了 0，这里必须用全新进程对象，否则期望值会被污染
f1 = Proc(pid=500, uid=1000, gid=1000)
f2 = Proc(pid=600, uid=1000, gid=1000)
d1, d2 = UnixSock("dgram", f1), UnixSock("dgram", f2)
try:
    peer_cred(d1)
    check("负向：未连接的 datagram socket 取不到 SO_PEERCRED", False, "没有抛异常")
except SockError:
    check("负向：未连接的 datagram socket 取不到 SO_PEERCRED", True, "SockError")
sp_a, sp_b = socketpair("dgram", f1, f2)
check("socketpair 的 datagram 端可以取（unix(7) 明确允许）",
      peer_cred(sp_a) == Ucred(600, 1000, 1000), str(peer_cred(sp_a)))
sp2_a, sp2_b = socketpair("stream", f1, f2)
check("socketpair 的 stream 端也可以取",
      peer_cred(sp2_a) == Ucred(600, 1000, 1000), str(peer_cred(sp2_a)))
f2.uid = 0
check("socketpair 同样是快照：创建后改 uid 不影响已建立的 peer_cred",
      peer_cred(sp2_a) == Ucred(600, 1000, 1000) and f2.uid == 0,
      str(peer_cred(sp2_a)))
check("SO_PEERCRED 是只读的",
      not hasattr(UnixSock("stream"), "set_peer_cred"))
try:
    sp2_a.setsockopt(SOL_SOCKET, SO_PEERCRED, 1)
    check("负向：setsockopt(SO_PEERCRED) 应当失败", False, "没有抛异常")
except SockError:
    check("负向：setsockopt(SO_PEERCRED) 应当失败", True, "SockError")

print("\n== 7. autobind：设了 SO_PASSCRED 且未显式 bind ==")
s = UnixSock("stream", p1)
check("初始没有地址", s.addr is None)
s.setsockopt(SOL_SOCKET, SO_PASSCRED, 1)
check("设 SO_PASSCRED 后自动绑定到抽象地址", s.autobound and s.addr is not None, str(s.addr))
kind, raw = s.addr
check("抽象地址以空字节开头", kind == "abstract" and raw[0] == 0, repr(raw))
check("后跟 5 个 [0-9a-f] 字符", len(raw) == 6 and all(
    chr(x) in AUTOBIND_CHARS for x in raw[1:]), repr(raw))
check("autobind 地址上限 2^20", AUTOBIND_LIMIT == 1 << 20, "%d" % AUTOBIND_LIMIT)
s2 = UnixSock("stream", p1)
s2.bind(path="/tmp/x.sock")
check("显式 bind 的是文件系统路径", s2.addr == ("path", "/tmp/x.sock"), str(s2.addr))
check("显式 bind 后 autobind 标记关闭", not s2.autobound)
s3 = UnixSock("stream", p1)
s3.bind(addrlen=ctypes.sizeof(ctypes.c_ushort))
check("addrlen == sizeof(sa_family_t) 也触发 autobind", s3.autobound, str(s3.addr))

print("\n== 8. AF_UNIX 的能力边界 ==")
a8, b8 = socketpair("stream", p1, p2)
try:
    sendmsg(a8, b"oob", flags=MSG_OOB)
    check("负向：AF_UNIX 不支持 MSG_OOB", False, "没有抛异常")
except SockError:
    check("负向：AF_UNIX 不支持 MSG_OOB", True, "SockError")
try:
    sendmsg(a8, b"more", flags=MSG_MORE)
    check("负向：AF_UNIX 不支持 MSG_MORE", False, "没有抛异常")
except SockError:
    check("负向：AF_UNIX 不支持 MSG_MORE", True, "SockError")
before = a8.rcvbuf
check("SO_RCVBUF 对 UNIX socket 无效",
      a8.setsockopt(SOL_SOCKET, SO_RCVBUF, 99999) is False and a8.rcvbuf == before,
      "rcvbuf=%d" % a8.rcvbuf)
check("SO_SNDBUF 对 UNIX socket 有效",
      a8.setsockopt(SOL_SOCKET, SO_SNDBUF, 4096) and a8.sndbuf == 4096,
      "sndbuf=%d" % a8.sndbuf)

print("\n== 9. 凭证是逐消息的：同一 socket 上不同发送方 ==")
srv = UnixSock("stream", server)
srv.setsockopt(SOL_SOCKET, SO_PASSCRED, 1)
for pid in (7001, 7002, 7003):
    cl = UnixSock("stream", Proc(pid=pid, uid=1000 + pid, gid=1000))
    cl.connect(srv)
    sendmsg(cl, b"ping")
seen = [c[2] for _, cms in srv.inbox for c in cms if c[1] == SCM_CREDENTIALS]
check("三条消息各带各发送方的 pid", [x.pid for x in seen] == [7001, 7002, 7003],
      str([x.pid for x in seen]))
check("uid 也各不相同", [x.uid for x in seen] == [8001, 8002, 8003],
      str([x.uid for x in seen]))

print("\n---- %d 项断言，失败 %d 项 ----" % (N, len(FAILED)))
if FAILED:
    for f in FAILED:
        print("  FAILED:", f)
    raise SystemExit(1)
print("ALL PASS")
