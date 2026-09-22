"""演示入口：io_uring 文件 IO。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ioring import (  # noqa: E402
    CQE, IORING_MAX_CQ_ENTRIES, IORING_MAX_ENTRIES, IORING_OFF_CQ_RING,
    IORING_OFF_SQES, IORING_OFF_SQ_RING, IORING_OP_READ,
    IORING_OP_READ_FIXED, IORING_SETUP_SQPOLL, OP_NAME,
)
from ioring_ring import (  # noqa: E402
    RegisteredBuffers, Ring, clamp_entries, mmap_length, prep_read,
    prep_read_plain, setup,
)


def hdr(t):
    print()
    print("== %s ==" % t)


hdr("三块 mmap 与上限")
print("  IORING_OFF_SQ_RING = 0x%X" % IORING_OFF_SQ_RING)
print("  IORING_OFF_CQ_RING = 0x%X" % IORING_OFF_CQ_RING)
print("  IORING_OFF_SQES    = 0x%X" % IORING_OFF_SQES)
print("  IORING_MAX_ENTRIES = %d, IORING_MAX_CQ_ENTRIES = %d"
      % (IORING_MAX_ENTRIES, IORING_MAX_CQ_ENTRIES))
for e in (8, 4096, 32768):
    r = setup(e)
    print("  entries=%-6d → sq=%-6d cq=%-6d  sq_ring=%-7d sqes=%-7d cq_ring=%d"
          % (e, r["sq_entries"], r["cq_entries"],
             mmap_length("sq_ring", r["sq_entries"], r["cq_entries"]),
             mmap_length("sqes", r["sq_entries"], r["cq_entries"]),
             mmap_length("cq_ring", r["sq_entries"], r["cq_entries"])))
print("  entries=65536 有 CLAMP → %d，无 CLAMP → EINVAL"
      % clamp_entries(65536, 1 << 4))

hdr("REGISTERED BUFFERS：addr 是索引，不是指针")
rb = RegisteredBuffers()
rb.register([bytearray(4096) for _ in range(4)])
fixed = prep_read(rb, fd=3, index=2, length=4096, off=4096, user_data=1)
plain = prep_read_plain(fd=3, addr=0x7F12_3456_0000, length=4096, off=4096,
                        user_data=2)
print("  %-14s opcode=%d addr=0x%X  buf_index=%d"
      % (OP_NAME[fixed.opcode], fixed.opcode, fixed.addr, fixed.buf_index))
print("  %-14s opcode=%d addr=0x%X  buf_index=%d"
      % (OP_NAME[plain.opcode], plain.opcode, plain.addr, plain.buf_index))
print("  >>> READ_FIXED(%d) 的 addr 是**缓冲区下标**；READ(%d) 的 addr 才是地址"
      % (IORING_OP_READ_FIXED, IORING_OP_READ))
print("  >>> 固定文件还要额外置 IOSQE_FIXED_FILE，否则 fd 被当成真实 fd")

hdr("CQE：res 是 -errno 的单通道")
for res in (4096, 0, -5, -11):
    c = CQE(1, res)
    print("  res=%-6d → ok=%-5s errno=%d" % (res, c.ok(), c.errno))

hdr("CQ 溢出（有/无 overflow 语义）")
for ov in (False, True):
    r = Ring(1, cq_overflow=ov)
    for i in range(1, 4):
        r.fill_cq(CQE(i, 1))
    print("  overflow=%-5s → ring 内 %d 个，overflow 队列 %d 个，丢弃 %d 个"
          % (ov, r.cq_pending(), len(r.overflow), r.dropped))

hdr("是否需要 io_uring_enter")
for sqpoll in (False, True):
    r = Ring(8, sqpoll=sqpoll)
    print("  SQPOLL=%-5s → 纯提交 %s，等待完成 %s"
          % (sqpoll, "需要" if r.need_syscall() else "不需要",
             "需要" if r.need_syscall(min_complete=1) else "不需要"))
print("  flag 值：IORING_SETUP_SQPOLL = 0x%X" % IORING_SETUP_SQPOLL)
