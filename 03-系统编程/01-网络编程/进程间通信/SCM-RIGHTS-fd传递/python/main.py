#!/usr/bin/env python3
"""SCM_RIGHTS / fd 传递 —— 可运行的自检 oracle。

README 里每一条"形式化断言"在这里都是一条真的断言。跑法：
    python3 main.py

模型见同目录 scm_model.py；C / Go 版本是同一套断言的移植。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scm_model import (  # noqa: E402
    EBADF, EINVAL, ETOOMANYREFS, MSG_CMSG_CLOEXEC, MSG_CTRUNC,
    SCM_MAX_FD, SCM_MAX_FD_OLD, FdTable, OpenFileDescription, ScmError,
    UnixSocket, cmsg_len, cmsg_space, fds_fit, fds_total_space,
)

from harness import check, open_file, pair, summary  # noqa: E402

# ================================================================ 场景 1
print("=== 1) 传的是 open file description 的引用（等价 dup），不是 fd 号 ===")
payload = b"HELLO-WORLD-0123456789"
ofd = open_file(payload)
sender = FdTable("sender", limit=64)
recv = FdTable("receiver", limit=64)

s_fd = sender.install(ofd)
# 接收方先占掉两个编号，好让它分配到的号与发送方不同 —— 编号是**每张表各自**的
recv.install(open_file(b"occupied-3"))
recv.install(open_file(b"occupied-4"))
print(f"  发送方 fd={s_fd} -> {sender.summary()}")
print(f"  发送方先读 5 字节: {ofd.read(5)!r}  → offset={ofd.offset}")

sock_a, sock_b = pair()
sent = sock_a.sendmsg(sender, b"!", [s_fd])
res = sock_b.recvmsg(recv, buf_size=16, ctrl_buf_size=64)
r_fd = res.fds[0]
print(f"  发送方 sendmsg(data={sent} B, fd={s_fd})")
print(f"  接收方 recvmsg → data={res.data!r} fds={res.fds} ({recv.summary()})")
print(f"  接收方读 5 字节: {recv.get(r_fd).ofd.read(5)!r}  → offset={ofd.offset}")

check("接收方分配到的 fd 号与发送方无关（编号是每张表各自的）", r_fd != s_fd,
      f"s={s_fd} r={r_fd}")
check("接收方读到的是 offset=5 之后的字节，说明共享同一个 OFD",
      recv.get(r_fd).ofd.offset == 10, f"offset={recv.get(r_fd).ofd.offset}")
check("两侧 fd 指向同一个 OFD 对象", recv.get(r_fd).ofd is ofd)

# 对照：按路径自己 open 会得到**另一个** OFD，offset 从 0 开始
fs = {"/tmp/demo.txt": payload}
other = open_file(fs["/tmp/demo.txt"])
after = other.read(5)
print(f"  对照：接收方按路径自己 open 再读 5 字节: {after!r}"
      f"  → offset={other.offset}")
check("按路径 re-open 得到独立 OFD，读出的内容完全不同", after != b"-WORL")
check("re-open 的 OFD 与传递得来的不是同一个对象", other is not ofd)

# 发送方 close 后接收方仍可用（引用计数：两张表各持一份引用）
before = ofd.refcount
sender.close(s_fd)
print(f"  发送方 close 掉 fd={s_fd} 前 refs={before}，"
      f"close 后 refs={ofd.refcount}，接收方仍可读 "
      f"{recv.get(r_fd).ofd.read(4)!r}")
check("两个进程各持一份引用：发送方 close 只掉 1", before == 2)
check("发送方 close 后接收方引用仍在", ofd.refcount == 1)

# ================================================================ 场景 2
print()
print(f"=== 2) 一次最多传的 fd 数：SCM_MAX_FD={SCM_MAX_FD}（<2.6.38 为 {SCM_MAX_FD_OLD}）===")
s2 = FdTable("s2", limit=4096)
r2 = FdTable("r2", limit=4096)
big = [s2.install(open_file(b"x")) for _ in range(SCM_MAX_FD)]
sock_a2, sock_b2 = pair()
sock_a2.sendmsg(s2, b"!", big)
res2 = sock_b2.recvmsg(r2, buf_size=8, ctrl_buf_size=fds_total_space(SCM_MAX_FD))
print(f"  传 {SCM_MAX_FD} 个 fd：发送成功，接收方装好 {len(res2.fds)} 个"
      f"（占控制缓冲 {fds_total_space(SCM_MAX_FD)} B）")
check(f"{SCM_MAX_FD} 个 fd 可以传", len(res2.fds) == SCM_MAX_FD,
      f"实际 {len(res2.fds)}")

extra = s2.install(open_file(b"x"))
try:
    sock_a2.sendmsg(s2, b"!", big + [extra])
    check(f"{SCM_MAX_FD + 1} 个 fd 应报 EINVAL", False, "居然没报错")
except ScmError as e:
    print(f"  传 {SCM_MAX_FD + 1} 个 fd：sendmsg 失败 → {e}")
    check(f"{SCM_MAX_FD + 1} 个 fd 报 EINVAL", e.errno_name == EINVAL, e.errno_name)

# ================================================================ 场景 3
print()
print("=== 3) 流式 socket 必须夹带 ≥1 字节真实数据；数据报不强制 ===")
s3 = FdTable("s3", limit=64)
f3 = s3.install(open_file(b"y"))
st_a, st_b = pair()
try:
    st_a.sendmsg(s3, b"", [f3])
    check("流式 socket 传 0 字节应报 EINVAL", False, "居然没报错")
except ScmError as e:
    print(f"  流式 socket sendmsg(data=0 B, fd) → {e}")
    check("流式 socket 传 0 字节报 EINVAL", e.errno_name == EINVAL, e.errno_name)

dg_a = UnixSocket("dgram")
dg_b = UnixSocket("dgram", peer=dg_a)
n = dg_a.sendmsg(s3, b"", [f3])
r3 = dg_b.recvmsg(FdTable("r3", 64), buf_size=8, ctrl_buf_size=64)
print(f"  数据报 socket sendmsg(data=0 B, fd) → 成功（{n} 字节），收到 {len(r3.fds)} 个 fd")
check("数据报 socket 允许 0 字节真实数据（但可移植代码仍应带 1 字节）",
      len(r3.fds) == 1)

# ================================================================ 场景 4
print()
print("=== 4) CMSG_SPACE vs CMSG_LEN：控制缓冲少算 4 字节就丢 fd ===")
print(f"  1 个 fd: cmsg_len = CMSG_LEN(4) = {cmsg_len(4)}"
      f"，但缓冲要留 CMSG_SPACE(4) = {cmsg_space(4)} B")
check("CMSG_SPACE 比 CMSG_LEN 多 4 字节对齐填充", cmsg_space(4) - cmsg_len(4) == 4)
for buf in (4, 20, 23, 24, 48):
    fit = fds_fit(buf, 1)
    print(f"  msg_controllen={buf:3d} → 能装 {fit} 个 fd")
check("按 sizeof(int)=4 预留缓冲 → 一个 fd 都收不到", fds_fit(4, 1) == 0)
check("按 CMSG_LEN=20 预留 → 仍然收不到（漏算填充）", fds_fit(20, 1) == 0)
check("按 CMSG_SPACE=24 预留 → 正好收到 1 个", fds_fit(24, 1) == 1)
print("  多 fd 时同样是 CMSG_SPACE(4n)：n=2 → "
      f"{fds_total_space(2)} B，n=3 → {fds_total_space(3)} B")
print(f"  ⚠ 对齐粒度：CMSG_SPACE(4)={cmsg_space(4)} 与 CMSG_SPACE(8)={cmsg_space(8)} "
      f"**相等** → 1 个 fd 和 2 个 fd 占的控制缓冲一样大")
check("1 个 fd 与 2 个 fd 所需的控制缓冲都是 24 B（对齐后合并）",
      cmsg_space(4) == cmsg_space(8) == 24)
check("因此按字节数反推「能装几个 fd」会算错：24 B 其实能装 2 个",
      fds_fit(24, 2) == 2 and fds_fit(24, 1) == 1)

# ================================================================ 场景 5
print()
print("=== 5) 控制缓冲过小 → MSG_CTRUNC，且多余 fd 在**接收方**被自动关闭 ===")
s5 = FdTable("s5", limit=64)
r5 = FdTable("r5", limit=64)
three = [s5.install(open_file(b"z")) for _ in range(3)]
ofds5 = [s5.get(fd).ofd for fd in three]
a5, b5 = pair()
a5.sendmsg(s5, b"!", three)
print(f"  发送后 refs（发送方表 1 份 + 在途 1 份）: {[o.refcount for o in ofds5]}")
res5 = b5.recvmsg(r5, buf_size=8, ctrl_buf_size=fds_total_space(2))   # 只够 2 个
print(f"  只给够 2 个 fd 的缓冲 → 收到 {len(res5.fds)} 个，"
      f"MSG_CTRUNC={bool(res5.msg_flags & MSG_CTRUNC)}，"
      f"被自动关闭 {res5.dropped_trunc} 个")
print(f"  接收后 refs: {[o.refcount for o in ofds5]}"
      f"  ← 前两个在接收方建了新引用(2)；第 3 个丢了在途引用且没建新引用(1)")
check("多余 fd 触发 MSG_CTRUNC", bool(res5.msg_flags & MSG_CTRUNC))
check("只装下 2 个 fd", len(res5.fds) == 2)
check("被关掉的第 3 个 fd 少了一份引用（接收方没留住它）",
      ofds5[2].refcount == 1, f"refs={ofds5[2].refcount}")
check("装下的前两个各多一份引用（接收方表持有）", ofds5[0].refcount == 2)

# 如果发送方事先撒手，被截断的那份引用就是**最后一份** → 对象当场释放
s5b = FdTable("s5b", limit=64)
r5b = FdTable("r5b", limit=64)
three_b = [s5b.install(open_file(b"z")) for _ in range(3)]
ofds5b = [s5b.get(fd).ofd for fd in three_b]
a5b, b5b = pair()
a5b.sendmsg(s5b, b"!", three_b)
for fd in three_b:
    s5b.close(fd)
print(f"  发送方发完就 close → 在途 refs: {[o.refcount for o in ofds5b]}"
      f"（只靠 socket 队列吊着）")
res5b = b5b.recvmsg(r5b, buf_size=8, ctrl_buf_size=fds_total_space(2))
print(f"  控制缓冲给 {fds_total_space(2)} B（够 2 个）→ 收到 {len(res5b.fds)} 个，"
      f"被关闭的第 3 个 refs={ofds5b[2].refcount} ← 归零即释放")
check("没有任何一方再持有 → 被截断的 fd 引用计数归零（对象释放）",
      ofds5b[2].refcount == 0, f"refs={ofds5b[2].refcount}")
check("留下来的那两个各被接收方表持有一份", 
      all(o.refcount == 1 for o in ofds5b[:2]))

# ================================================================ 场景 6
print()
print("=== 6) 接收方 RLIMIT_NOFILE 不足 → 同样自动关闭，且不报错 ===")
s6 = FdTable("s6", limit=64)
r6 = FdTable("r6", limit=3)          # 只剩很少槽位
r6.install(open_file(b"occupied"))   # 先占掉一个
ten = [s6.install(open_file(b"q")) for _ in range(10)]
ofds6 = [s6.get(fd).ofd for fd in ten]
a6, b6 = pair()
a6.sendmsg(s6, b"!", ten)
res6 = b6.recvmsg(r6, buf_size=8, ctrl_buf_size=fds_total_space(10))
print(f"  RLIMIT_NOFILE=3（已用 1）→ 装好 {len(res6.fds)} 个，"
      f"因限流自动关闭 {res6.dropped_rlimit} 个，recvmsg 本身**没有**报错")
print(f"  接收方表: {r6.summary()}")
check("recvmsg 不因 RLIMIT_NOFILE 报错（静默丢弃）", True)
check("装下的数量不超过 RLIMIT_NOFILE 剩余槽位",
      len(res6.fds) <= r6.limit - 1, f"{len(res6.fds)}")
check("超出限流的 fd 被自动关闭", res6.dropped_rlimit >= 1,
      f"dropped={res6.dropped_rlimit}")

# ================================================================ 场景 7
print()
print("=== 7) 在途（in-flight）fd 记账：Linux 4.5 起会报 ETOOMANYREFS ===")
s7 = FdTable("s7", limit=8)
a7, b7 = pair()                      # b7 一直不 recvmsg，制造"在途"
err7 = None
table_always_empty = True
for i in range(1, 13):
    fd = s7.install(open_file(b"f"))
    try:
        a7.sendmsg(s7, b"!", [fd])
    except ScmError as e:
        err7 = e
        s7.close(fd)
        print(f"  第 {i} 次 sendmsg（在途已 {a7.in_flight()}）→ {e}")
        break
    s7.close(fd)                     # 关键：发完立刻 close，旧内核靠这招绕过记账
    if s7.size() != 0 or a7.in_flight() != i:
        table_always_empty = False
    print(f"  第 {i} 次 sendmsg 成功，发送方 close 掉本地 fd，"
          f"在途={a7.in_flight()}，发送方表={s7.size()} 项")
check("在途 fd 超限报 ETOOMANYREFS",
      err7 is not None and err7.errno_name == ETOOMANYREFS,
      err7.errno_name if err7 else "没报错")
check("旧内核漏洞的成因：发送方 fd 表恒空而在途量持续累积到 RLIMIT_NOFILE",
      table_always_empty and a7.in_flight() == 8)
print(f"  −> Linux 4.5 之前这里能无限发下去（发送方表恒空，RLIMIT_NOFILE 形同虚设）；"
      f"4.5 起按在途量记账并报 ETOOMANYREFS，除非有 CAP_SYS_RESOURCE")
# 对端把消息全接走之后在途量才清零（一条消息一次 recvmsg：控制数据是屏障）
r7 = FdTable("r7", 4096)
while b7.recvmsg(r7, buf_size=8, ctrl_buf_size=64) is not None:
    pass
print(f"  对端逐条 recvmsg 接走后在途={a7.in_flight()}")
check("对端接走后在途量清零", a7.in_flight() == 0)

# ================================================================ 场景 8
print()
print("=== 8) 控制数据是屏障：man7 unix(7) 的 4 / 1 / 4 字节例子 ===")
s8 = FdTable("s8", limit=64)
f8 = s8.install(open_file(b"m"))
a8, b8 = pair()
a8.sendmsg(s8, b"AAAA", [])          # (1) 4 字节，无控制数据
a8.sendmsg(s8, b"B", [f8])           # (2) 1 字节 + 控制数据
a8.sendmsg(s8, b"CCCC", [])          # (3) 4 字节，无控制数据
r8 = FdTable("r8", limit=64)
first = b8.recvmsg(r8, buf_size=20, ctrl_buf_size=64)
second = b8.recvmsg(r8, buf_size=20, ctrl_buf_size=64)
third = b8.recvmsg(r8, buf_size=20, ctrl_buf_size=64)
third_data = third.data if third else None
print(f"  第 1 次 recvmsg(buf=20) → {first.data!r} + {len(first.fds)} 个 fd")
print(f"  第 2 次 recvmsg(buf=20) → {second.data!r}")
print(f"  第 3 次 recvmsg(buf=20) → {third_data!r}（队列已空则 None）")
check("第 1 次拿到 5 字节（4+1）并在同一调用里拿到 fd",
      first.data == b"AAAAB" and len(first.fds) == 1, f"{first.data!r}")
check("屏障挡住了后面那 4 字节", second.data == b"CCCC", f"{second.data!r}")

# ================================================================ 场景 9
print()
print("=== 9) MSG_CMSG_CLOEXEC（Linux 2.6.23）：消除 exec 泄漏窗口 ===")
s9 = FdTable("s9", limit=64)
a9, b9 = pair()
r9a = FdTable("r9a", limit=64)
r9b = FdTable("r9b", limit=64)
f9 = s9.install(open_file(b"c"))
a9.sendmsg(s9, b"!", [f9])
plain = b9.recvmsg(r9a, buf_size=8, ctrl_buf_size=64)
a9.sendmsg(s9, b"!", [f9])
withflag = b9.recvmsg(r9b, buf_size=8, ctrl_buf_size=64, flags=MSG_CMSG_CLOEXEC)
print(f"  不带 flag → FD_CLOEXEC={r9a.get(plain.fds[0]).cloexec}")
print(f"  带 MSG_CMSG_CLOEXEC → FD_CLOEXEC={r9b.get(withflag.fds[0]).cloexec}")
check("不带 flag 时收到的新 fd 不带 CLOEXEC（要自己 fcntl，存在竞态）",
      r9a.get(plain.fds[0]).cloexec is False)
check("MSG_CMSG_CLOEXEC 原子地设好 CLOEXEC", r9b.get(withflag.fds[0]).cloexec is True)

# ================================================================ 场景 10
print()
print("=== 10) 发送一个不存在的 fd → EBADF ===")
s10 = FdTable("s10", limit=64)
a10, _ = pair()
try:
    a10.sendmsg(s10, b"!", [42])
    check("发送无效 fd 应报 EBADF", False, "居然没报错")
except ScmError as e:
    print(f"  sendmsg(fd=42，本进程没有) → {e}")
    check("发送无效 fd 报 EBADF", e.errno_name == EBADF, e.errno_name)

sys.exit(summary())
