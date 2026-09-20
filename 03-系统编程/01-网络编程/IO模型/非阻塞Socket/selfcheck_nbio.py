# -*- coding: utf-8 -*-
"""非阻塞 IO 与 EAGAIN 语义 —— 自检

判据全部来自 man7 页面原文（read(2) / recv(2) / socket(7)），
断言的是「误报集 / 漏报集」而不是一句「应然」：
  * 负向：非阻塞且缓冲为空时必须**不是** Ok，且 errno 必须是 EAGAIN（不能是别的）
  * 负向：阻塞且缓冲为空时必须**不是** EAGAIN，而是真的挂起并等到数据
  * 负向：`recv(n)` 不得因为 n 未满而挂起（短读是规范明文）
"""
from nbio_model import *  # noqa: F401,F403

N = 0
FAILED = []


def check(label, cond, detail=""):
    global N
    N += 1
    if cond:
        print("ok   %-58s %s" % (label, detail))
    else:
        FAILED.append(label)
        print("FAIL %-58s %s" % (label, detail))


print("== 1. errno 编号与措辞 ==")
check("Linux 上 EAGAIN 与 EWOULDBLOCK 同值", EAGAIN == EWOULDBLOCK == 11, "EAGAIN=%d" % EAGAIN)
check("socket fd 的报错措辞是 EAGAIN or EWOULDBLOCK",
      errno_wording(Sock(kind="socket")) == "EAGAIN or EWOULDBLOCK")
check("非 socket fd 的报错措辞只有 EAGAIN",
      errno_wording(Sock(kind="pipe")) == "EAGAIN",
      "read(2) 把两种 fd 分成了两条 ERRORS 条目")

print("\n== 2. 读：阻塞 vs 非阻塞 ==")
net = Net()
sb = Sock(nonblock=False)
sb.incoming.append(b"hello")
r = recv(net, sb, 16)
check("阻塞 recv 缓冲为空 → 挂起后拿到数据", r.ok and r.val == b"hello", repr(r))
check("阻塞路径真的发生了一次挂起", net.waits == 1, "waits=%d" % net.waits)

net2 = Net()
sn = Sock(nonblock=True)
r = recv(net2, sn, 16)
check("非阻塞 recv 缓冲为空 → 不是 Ok", not r.ok, repr(r))
check("非阻塞 recv 的 errno 恰为 EAGAIN 而非其它", r.errno == EAGAIN and r.errno != 5, "errno=%d" % r.errno)
check("非阻塞路径零挂起", net2.waits == 0, "waits=%d" % net2.waits)

print("\n== 3. 短读：recv 绝不等满请求长度 ==")
net3 = Net()
s3 = Sock(nonblock=True)
net3.arrive(s3, b"abcd")
r = recv(net3, s3, 10)
check("请求 10 字节但只有 4 字节可用 → 返回 4 字节", r.ok and r.val == b"abcd" and len(r.val) == 4, repr(r))
check("短读不产生挂起", net3.waits == 0)
r2 = recv(net3, s3, 10)
check("读空后再读 → EAGAIN", (not r2.ok) and r2.errno == EAGAIN, repr(r2))

print("\n== 4. MSG_DONTWAIT：按调用覆盖 fd 标志 ==")
net4 = Net()
s4 = Sock(nonblock=False)          # fd 本身是阻塞的
r = recv(net4, s4, 16, flags=MSG_DONTWAIT)
check("阻塞 fd + MSG_DONTWAIT → EAGAIN", (not r.ok) and r.errno == EAGAIN, repr(r))
check("MSG_DONTWAIT 不挂起", net4.waits == 0)
net4.arrive(s4, b"x")
r = recv(net4, s4, 16, flags=MSG_DONTWAIT)
check("有数据时 MSG_DONTWAIT 照样正常返回", r.ok and r.val == b"x", repr(r))

print("\n== 5. 写：部分写与 EAGAIN ==")
net5 = Net()
s5 = Sock(nonblock=True, snd_room=100)
r = send(net5, s5, b"z" * 300)
check("发送缓冲只剩 100 → 部分写返回 100", r.ok and r.val == 100, repr(r))
check("部分写后剩余空间归零", s5.snd_room == 0)
r = send(net5, s5, b"z")
check("缓冲满 + 非阻塞 → EAGAIN（不是 0 字节成功）", (not r.ok) and r.errno == EAGAIN, repr(r))

net5b = Net()
s5b = Sock(nonblock=False, snd_room=0)
r = send(net5b, s5b, b"payload")
check("缓冲满 + 阻塞 → 挂起后写成", r.ok and r.val == len(b"payload"), repr(r))
check("阻塞写确实挂起了一次", net5b.waits == 1)

print("\n== 6. accept 的空队列语义 ==")
net6 = Net()
l6 = Sock(nonblock=True)
listen(l6)
r = accept(net6, l6)
check("非阻塞 accept 空队列 → EAGAIN", (not r.ok) and r.errno == EAGAIN, repr(r))
net6.arrive(l6, b"")
l6.backlog.append("conn-1")
r = accept(net6, l6)
check("队列有连接 → 立即返回", r.ok and r.val == "conn-1", repr(r))
r = accept(net6, l6)
check("取空后再 accept → 回到 EAGAIN", (not r.ok) and r.errno == EAGAIN, repr(r))

print("\n== 7. 非阻塞 connect 的三态 ==")
net7 = Net()
c = Sock(nonblock=True)
r = connect(net7, c)
check("非阻塞 connect → EINPROGRESS（不是 0）", (not r.ok) and r.errno == EINPROGRESS, repr(r))
check("返回 EINPROGRESS 时连接处于 SYN_SENT", c.state == "SYN_SENT")
r = connect(net7, c)
check("握手未完成再 connect → EALREADY", (not r.ok) and r.errno == EALREADY, repr(r))
net7._block(c)
check("阻塞等待后进入 ESTABLISHED", c.state == "ESTABLISHED")
r = connect(net7, c)
check("已建立再 connect → EISCONN", (not r.ok) and r.errno == EISCONN, repr(r))

c2 = Sock(nonblock=True)
c2.so_error = ECONNRESET
net7._block(c2)
r = getsockopt_so_error(net7, c2)
check("非阻塞 connect 的失败只能从 SO_ERROR 取", r.ok and r.val == ECONNRESET, repr(r))

print("\n== 8. 忙轮询的代价：空转系统调用次数 ==")
net8 = Net()
p = Sock(nonblock=True)
arrivals = [b"", b"", b"", b"A" * 100, b"B" * 100]
got, spins = busy_poll_recv(net8, p, 200, arrivals)
check("忙轮询拿到了全部 200 字节", len(got) == 200, "len=%d" % len(got))
check("忙轮询期间发生过 EAGAIN 空转", spins > 5, "spins=%d" % spins)

net9 = Net()
p9 = Sock(nonblock=False)
got9, agains = epoll_then_recv(net9, p9, 200, arrivals)
check("就绪通知写法同样拿到 200 字节", len(got9) == 200, "len=%d" % len(got9))
check("就绪通知写法的挂起次数严格更少", net9.waits < spins,
      "epoll waits=%d vs busy spins=%d" % (net9.waits, spins))

print("\n== 9. 二者数据面必须等价（只改等待方式不改结果） ==")
check("两种写法读到的字节序列完全相同", got == got9, "%r == %r" % (got[:12], got9[:12]))

print("\n---- %d 项断言，失败 %d 项 ----" % (N, len(FAILED)))
if FAILED:
    for f in FAILED:
        print("  FAILED:", f)
    raise SystemExit(1)
print("ALL PASS")
