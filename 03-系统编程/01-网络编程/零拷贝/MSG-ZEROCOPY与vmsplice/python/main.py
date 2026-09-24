"""演示入口:MSG_ZEROCOPY 的通知记账与 vmsplice 的入参校验。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zcopy_model import (                                 # noqa: E402
    ZerocopySocket, IOV_MAX, PAGE_SIZE, UINT32_MAX,
    SO_EE_ORIGIN_ZEROCOPY, SO_EE_CODE_ZEROCOPY_COPIED,
    ENOBUFS, EBADF, EINVAL, EAGAIN,
    SPLICE_F_GIFT, vmsplice, vmsplice_direction, zerocopy_is_worth_it,
)


def main():
    print("== ① 必须先 setsockopt(SO_ZEROCOPY)声明意图 ==")
    s = ZerocopySocket()
    print("  未开:send(4096, MSG_ZEROCOPY) -> %r  (内核静默忽略 flag)" % (s.send(4096),))
    s.setsockopt_zerocopy(1)
    print("  开了:send(4096, MSG_ZEROCOPY) -> %r  (分配 counter)" % (s.send(4096),))
    print("  开了但不带 flag            -> %r  (混合使用是安全的)" % (s.send(4096, zerocopy=False),))

    print("\n== ② counter 按「调用」计数,不按字节 ==")
    c = ZerocopySocket(zc_enabled=True)
    print("  %-12s %-14s %s" % ("send 长度", "返回值", "counter"))
    for ln, kw in ((100000, {}), (1, {}), (0, {}), (4096, {"enobufs": True}), (4096, {})):
        ret, ctr = c.send(ln, **kw)
        print("  %-12d %-14d %s" % (ln, ret, ctr))

    print("\n== ③ u32 回绕 ==")
    w = ZerocopySocket(zc_enabled=True)
    w.counter = UINT32_MAX - 1
    print("  %d -> %d -> %d -> %d"
          % (UINT32_MAX - 1, w.send(1)[1], w.send(1)[1], w.send(1)[1]))

    print("\n== ④ 通知合并:[ee_info, ee_data] 闭区间 ==")
    for vals in ((1, 2, 3), (1, 2, 4), (UINT32_MAX, 0)):
        q = ZerocopySocket(zc_enabled=True)
        marks = []
        for v in vals:
            marks.append("新包" if q.complete(v) else "合并")
        out = []
        while q.outstanding():
            n = q.recv_errqueue()
            out.append("[%d,%d]" % (n["ee_info"], n["ee_data"]))
        print("  通知序列 %-22s -> %-12s -> 队列 %s"
              % (str(list(vals)), ",".join(marks), " ".join(out)))

    print("\n== ⑤ 通知字段(sock_extended_err) ==")
    z = ZerocopySocket(zc_enabled=True)
    z.complete(7)
    print("  %r" % z.recv_errqueue())
    print("  ee_errno 恒为 0 是刻意的:否则会阻塞该 socket 上的 send/recv")

    print("\n== ⑥ 拷贝退化:loopback 一定走 deferred copy ==")
    for lb in (False, True):
        g = ZerocopySocket(zc_enabled=True, loopback=lb)
        g.complete(1)
        print("  loopback=%-6s ee_code=%d%s"
              % (lb, g.recv_errqueue()["ee_code"],
                 "  <- SO_EE_CODE_ZEROCOPY_COPIED" if lb else ""))

    print("\n== ⑦ 量级:文档说「> around 10 KB 才划算」 ==")
    print("  %-12s %s" % ("写入长度", "值得开?"))
    for ln in (1024, 10 * 1024, 64 * 1024):
        print("  %-12d %s" % (ln, zerocopy_is_worth_it(ln)))

    print("\n== ⑧ vmsplice 入参校验(IOV_MAX=%d, 页=%d) ==" % (IOV_MAX, PAGE_SIZE))
    cases = (
        ("fd 不是 pipe", False, [(0, 8)], 0, False),
        ("nr_segs = IOV_MAX+1", True, [(0, 1)] * (IOV_MAX + 1), 0, False),
        ("GIFT 未对齐基址", True, [(PAGE_SIZE + 1, PAGE_SIZE)], SPLICE_F_GIFT, False),
        ("GIFT 未对齐长度", True, [(PAGE_SIZE, PAGE_SIZE - 1)], SPLICE_F_GIFT, False),
        ("GIFT 全对齐", True, [(PAGE_SIZE, PAGE_SIZE)], SPLICE_F_GIFT, False),
        ("不带 GIFT 任意对齐", True, [(PAGE_SIZE + 1, 7)], 0, False),
    )
    print("  %-24s %s" % ("用例", "(返回值, errno)"))
    for name, ispipe, iovs, flags, wb in cases:
        print("  %-24s %r" % (name, vmsplice(ispipe, iovs, flags, wb)))

    print("\n== ⑨ vmsplice 的方向不对称 ==")
    print("  fd 写端 -> %s(真把用户页映射进管道)"
          % vmsplice_direction(True))
    print("  fd 读端 -> %s(实际只是拷贝到用户空间,接口对称而已)"
          % vmsplice_direction(False))


if __name__ == "__main__":
    main()
