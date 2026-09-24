"""自检:TCP 紧急数据模型。纯计算断言。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from urg_model import (                                   # noqa: E402
    TcpUrgSock, before, after, MASK,
    TCP_URG_VALID, TCP_URG_NOTYET, TCP_URG_READ,
    EINVAL, ENOTCONN, MSG_OOB, MSG_TRUNC, EPOLLPRI,
)

N = 0


def ok(cond, msg):
    global N
    N += 1
    if not cond:
        raise AssertionError("FAIL #%d: %s" % (N, msg))


def eq(got, want, msg):
    ok(got == want, "%s -> got %r want %r" % (msg, got, want))


# ------------------------------------------------------------ 序列号比较
ok(before(1, 2), "before(1,2)")
ok(not after(1, 2), "after(1,2) 为假")
ok(after(2, 1), "after(2,1)")
ok(not before(1, 1), "before(x,x) 为假")
ok(before(0xFFFFFFFF, 0), "u32 回绕:0xFFFFFFFF 在 0 之前")
ok(after(0, 0xFFFFFFFF), "0 在 0xFFFFFFFF 之后")
ok(not before(0, 0xFFFFFFFF), "反向不成立")

# ------------------------------------------------------------ 三态常量
eq(TCP_URG_VALID, 0x100, "TCP_URG_VALID")
eq(TCP_URG_NOTYET, 0x200, "TCP_URG_NOTYET")
eq(TCP_URG_READ, 0x400, "TCP_URG_READ")

# --------------------------------------------- 基本流程(BSD / Linux 默认解释)
a = TcpUrgSock(copied_seq=100, rcv_nxt=100)
r = a.on_segment(seq=100, urg_ptr=1, payload=b"ABC")
eq(r["result"], "accepted", "默认解释正常受理")
eq(r["sigurg"], True, "发 SIGURG")
eq(a.sigurg, 1, "sigurg 计数")
eq(a.urg_seq, 100, "urg_ptr=1 减 1 后指向 seq 100,即紧急数据最后一字节")
eq(a.urg_data, TCP_URG_VALID | 0x41, "取出字节 'A'")
eq(a.sockatmark(), 1, "copied_seq 正好等于 urg_seq -> 在标记处")
# rcv_nxt 推进后 SIOCINQ 反而报告 0
a.rcv_nxt = 103
eq(a.inq(), 0, "有未处理紧急数据时 SIOCINQ 被截断到标记处 -> 0(负控:不是 3)")
eq(a.recv_limit(100, 3), 0, "普通读也被截断到 0 字节")

# 成对用例:SO_OOBINLINE 时紧急字节并入正常流,不再截断
b = TcpUrgSock(copied_seq=100, rcv_nxt=100, oobinline=True)
b.on_segment(seq=100, urg_ptr=1, payload=b"ABC")
b.rcv_nxt = 103
eq(b.inq(), 3, "URGINLINE -> SIOCINQ 报 3")
eq(b.recv_limit(100, 3), 0, "URGINLINE **也**会被 urg_offset 截断(与直觉相反)")
eq(b.read_chunk(100, 3), (3, 0), "URGINLINE:紧急字节当普通数据交上去,无洞")
eq(a.read_chunk(100, 3), (2, 1), "非 URGINLINE:紧急字节被跳过,urg_hole=1(成对用例)")

# ------------------------------------------------- tcp_stdurg / RFC 1122 解释
c = TcpUrgSock(copied_seq=100, rcv_nxt=100, stdurg=True)
c.on_segment(seq=100, urg_ptr=1, payload=b"ABC")
eq(c.urg_seq, 101, "stdurg=1 时不减 1,指针指向 seq 101")
eq(c.urg_data, TCP_URG_VALID | 0x42, "RFC 1122 解释下取到的是 'B'")
c.rcv_nxt = 103
eq(c.sockatmark(), 0, "urg_seq(101) != copied_seq(100) -> 不在标记处")
eq(c.inq(), 1, "截断到标记处 -> 1")
eq(c.recv_limit(100, 3), 1, "普通读只能读 1 字节")
eq(c.read_chunk(100, 3), (1, 0), "紧急字节在中间:只读到它之前,无洞")
c.oobinline = True
eq(c.read_chunk(100, 3), (1, 0), "URGINLINE 对中间位置同样无差别(成对用例)")
c.oobinline = False

# -------------------------------------------------------------- 三条守卫
d = TcpUrgSock(copied_seq=100, rcv_nxt=105)
rd = d.on_segment(seq=100, urg_ptr=1, payload=b"ABC")
eq(rd["result"], "ignored_replay", "紧急指针指向已收过的字节 -> 不回放")
eq(rd["sigurg"], False, "不回放就不发 SIGURG")
eq(d.sigurg, 0, "sigurg 未计数")
eq(d.urg_data, 0, "urg_data 保持 0")

e = TcpUrgSock(copied_seq=100, rcv_nxt=100)
e.urg_data = TCP_URG_NOTYET
e.urg_seq = 102
re1 = e.on_segment(seq=100, urg_ptr=3, payload=b"ABCDE")   # ptr = 102
eq(re1["result"], "ignored_duplicate", "指针不比已有的更新 -> 忽略")
re2 = e.on_segment(seq=100, urg_ptr=4, payload=b"ABCDE")   # ptr = 103
eq(re2["result"], "accepted", "指针更新一位 -> 受理(成对用例)")
eq(e.urg_seq, 103, "urg_seq 更新")

f = TcpUrgSock(copied_seq=200, rcv_nxt=200)
rf = f.on_segment(seq=100, urg_ptr=1, payload=b"ABC")      # ptr = 100
eq(rf["result"], "ignored_already_read", "指针已被读过 -> after(copied_seq, ptr)")
eq(f.urg_data, 0, "状态不变")

# ----------------------------------------------------- "Double Dutch" 修正
g = TcpUrgSock(copied_seq=100, rcv_nxt=103)
g.urg_data = TCP_URG_NOTYET
g.urg_seq = 100
rg = g.on_segment(seq=103, urg_ptr=1, payload=b"XY")
eq(rg["result"], "accepted_advance_copied_seq", "上一個紧急字节刚读过 -> 推进 copied_seq")
eq(g.copied_seq, 101, "copied_seq 100 -> 101")
eq(g.urg_seq, 103, "新的 urg_seq")
eq(g.urg_data, TCP_URG_VALID | 0x58, "取出 'X'")

h = TcpUrgSock(copied_seq=100, rcv_nxt=103, oobinline=True)
h.urg_data = TCP_URG_NOTYET
h.urg_seq = 100
rh = h.on_segment(seq=103, urg_ptr=1, payload=b"XY")
eq(rh["result"], "accepted", "URGINLINE 时不走该修正(成对用例)")
eq(h.copied_seq, 100, "copied_seq 不动")

# ------------------------------------------------------------ tcp_recv_urg
i = TcpUrgSock(copied_seq=100, rcv_nxt=103)
i.urg_data = TCP_URG_VALID | 0x41
i.urg_seq = 100
eq(i.recv_oob(), (1, MSG_OOB), "带 MSG_OOB 读到 1 字节")
eq(i.urg_data, TCP_URG_READ, "读后置 TCP_URG_READ")
eq(i.recv_oob(), (-EINVAL, 0), "urg_data == TCP_URG_READ -> EINVAL")
j = TcpUrgSock(copied_seq=100, rcv_nxt=103)
j.urg_data = TCP_URG_VALID | 0x41
j.urg_seq = 100
eq(j.recv_oob(peek=True), (1, MSG_OOB), "MSG_PEEK 也能读")
eq(j.urg_data, TCP_URG_VALID | 0x41, "MSG_PEEK 不改状态")
eq(j.recv_oob(length=0), (0, MSG_OOB | MSG_TRUNC), "len=0 -> 返回 0 且带 MSG_TRUNC")
k = TcpUrgSock(copied_seq=100, rcv_nxt=103, oobinline=True)
k.urg_data = TCP_URG_VALID | 0x41
k.urg_seq = 100
eq(k.recv_oob(), (-EINVAL, 0), "URGINLINE 下用 MSG_OOB 读 -> EINVAL")
m = TcpUrgSock(copied_seq=100, rcv_nxt=103)
eq(m.recv_oob(), (-EINVAL, 0), "没有紧急数据 -> EINVAL")
n = TcpUrgSock(copied_seq=100, rcv_nxt=103)
n.urg_data = TCP_URG_NOTYET        # 还没收到那个字节
n.urg_seq = 100
eq(n.recv_oob(), (0, 0), "NOTYET(字节未到)不是 EINVAL,而是返回 0")
o = TcpUrgSock(copied_seq=100, rcv_nxt=103, state="CLOSE")
o.urg_data = TCP_URG_VALID | 0x41
o.urg_seq = 100
eq(o.recv_oob(), (-ENOTCONN, 0), "CLOSE 且未 DONE -> ENOTCONN")

# ------------------------------------------------------------- tcp_poll
p = TcpUrgSock(copied_seq=100, rcv_nxt=103)
p.urg_data = TCP_URG_VALID | 0x41
p.urg_seq = 100
mask, target = p.epoll_mask(target=1)
eq(mask, EPOLLPRI, "TCP_URG_VALID -> EPOLLPRI")
eq(target, 2, "在标记处且非 URGINLINE -> rcvlowat 目标 +1")
p.urg_data = TCP_URG_NOTYET
mask, target = p.epoll_mask(target=1)
eq(mask, 0, "仅 NOTYET 不给 EPOLLPRI(负控)")
p.urg_data = TCP_URG_VALID | 0x41
p.oobinline = True
mask, target = p.epoll_mask(target=1)
eq(target, 1, "URGINLINE 时目标不 +1")

# ------------------------------------------------ sockatmark 不摘除标记
q = TcpUrgSock(copied_seq=100, rcv_nxt=103)
q.urg_data = TCP_URG_VALID | 0x41
q.urg_seq = 100
eq(q.sockatmark(), 1, "在标记处")
eq(q.sockatmark(), 1, "重复调用仍是 1,不摘除标记")
q.copied_seq = 101
eq(q.sockatmark(), 0, "读过之后离开标记")

print("selfcheck OK: %d assertions" % N)
