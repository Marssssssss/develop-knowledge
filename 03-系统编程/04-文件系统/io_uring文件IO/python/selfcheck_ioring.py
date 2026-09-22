"""io_uring 文件 IO 自检：把头文件与 man page 的条文变成断言。"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ioring import (  # noqa: E402
    CQE, CQE_FMT, CQE_SIZE, CQE_SIZE_32, IORING_MAX_CQ_ENTRIES,
    IORING_MAX_ENTRIES, IORING_OFF_CQ_RING, IORING_OFF_MMAP_MASK,
    IORING_OFF_PBUF_RING, IORING_OFF_SQES, IORING_OFF_SQ_RING,
    IORING_OP_FTRUNCATE, IORING_OP_READ, IORING_OP_READV_FIXED,
    IORING_OP_READ_FIXED, IORING_OP_WRITE_FIXED,
    IORING_REGISTER_BUFFERS, IORING_REGISTER_BUFFERS2,
    IORING_REGISTER_BUFFERS_UPDATE, IORING_REGISTER_FILES,
    IORING_SETUP_CLAMP, IORING_SETUP_CQE32, IORING_SETUP_CQSIZE,
    IORING_SETUP_SQPOLL, IORING_SETUP_SQE128, IORING_UNREGISTER_BUFFERS,
    IOSQE_ASYNC, IOSQE_ASYNC_BIT, IOSQE_BUFFER_SELECT,
    IOSQE_CQE_SKIP_SUCCESS, IOSQE_FIXED_FILE, IOSQE_IO_DRAIN,
    IOSQE_IO_HARDLINK, IOSQE_IO_LINK, OP_NAME, SQE, SQE_SIZE, SQE_SIZE_128,
)
from ioring_ring import (  # noqa: E402
    RegisteredBuffers, Ring, clamp_entries, mmap_length, prep_read,
    prep_read_plain, setup,
)

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %-58s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-58s %s" % (label, detail))


print("== 1. mmap 偏移与上限（io_uring.h / io_uring/io_uring.h） ==")
check("IORING_OFF_SQ_RING == 0", IORING_OFF_SQ_RING == 0)
check("IORING_OFF_CQ_RING == 0x8000000", IORING_OFF_CQ_RING == 0x8000000)
check("IORING_OFF_SQES == 0x10000000", IORING_OFF_SQES == 0x10000000)
check("IORING_OFF_PBUF_RING == 0x80000000", IORING_OFF_PBUF_RING == 0x80000000)
check("IORING_OFF_MMAP_MASK == 0xf8000000", IORING_OFF_MMAP_MASK == 0xF8000000)
check("三个偏移互不相同且都被 MMAP_MASK 覆盖",
      len({IORING_OFF_SQ_RING, IORING_OFF_CQ_RING, IORING_OFF_SQES,
           IORING_OFF_PBUF_RING}) == 4 and
      all(o & IORING_OFF_MMAP_MASK == o for o in
          (IORING_OFF_SQ_RING, IORING_OFF_CQ_RING, IORING_OFF_SQES)))
check("IORING_MAX_ENTRIES == 32768", IORING_MAX_ENTRIES == 32768)
check("IORING_MAX_CQ_ENTRIES == 2*32768 = 65536",
      IORING_MAX_CQ_ENTRIES == 65536)

print("== 2. entries 钳制（io_uring_setup(2)） ==")
check("entries=8 正常", clamp_entries(8) == 8)
check("entries=32768 是上限", clamp_entries(32768) == 32768)
try:
    clamp_entries(32769)
    check("entries>MAX 无 CLAMP → EINVAL", False)
except ValueError:
    check("entries>MAX 无 CLAMP → EINVAL", True)
check("entries>MAX 有 CLAMP → 钳到 32768",
      clamp_entries(1 << 20, IORING_SETUP_CLAMP) == 32768)
try:
    clamp_entries(0)
    check("entries=0 → EINVAL", False)
except ValueError:
    check("entries=0 → EINVAL", True)
r = setup(64)
check("默认 cq_entries = 2*sq_entries", r["cq_entries"] == 128)
r = setup(64, cq_entries=1024, flags=IORING_SETUP_CQSIZE)
check("CQSIZE 时 cq_entries 可大于 2*entries", r["cq_entries"] == 1024)
try:
    setup(64, cq_entries=32, flags=IORING_SETUP_CQSIZE)
    check("CQSIZE 但 cq_entries <= entries → EINVAL", False)
except ValueError:
    check("CQSIZE 但 cq_entries <= entries → EINVAL", True)
try:
    setup(64, cq_entries=1024)
    check("给 cq_entries 却没给 CQSIZE → EINVAL", False)
except ValueError:
    check("给 cq_entries 却没给 CQSIZE → EINVAL", True)
r = setup(64, cq_entries=1 << 20, flags=IORING_SETUP_CQSIZE)
check("cq_entries 超 MAX_CQ_ENTRIES → 钳到 65536", r["cq_entries"] == 65536)

print("== 3. SQE/CQE 尺寸与位宽 ==")
check("SQE 64 字节 / CQE 16 字节", SQE_SIZE == 64 and CQE_SIZE == 16)
check("SQE128 → 128，CQE32 → 32", SQE_SIZE_128 == 128 and CQE_SIZE_32 == 32)
r = setup(64, flags=IORING_SETUP_SQE128 | IORING_SETUP_CQE32)
check("SQE128|CQE32 时尺寸变化", r["sqe_size"] == 128 and r["cqe_size"] == 32)
check("CQE 结构 16 字节（u64+s32+u32 无填充）",
      struct.calcsize(CQE_FMT) == 16)
check("IOSQE_FIXED_FILE 是 bit0", IOSQE_FIXED_FILE == 1)
check("IOSQE_IO_DRAIN 是 bit1（在 FIXED_FILE 之后插入的）",
      IOSQE_IO_DRAIN == 1 << 1)
check("IOSQE_IO_LINK 是 bit2 不是 bit1", IOSQE_IO_LINK == 1 << 2)
check("IOSQE_IO_HARDLINK 是 bit3", IOSQE_IO_HARDLINK == 1 << 3)
check("IOSQE_ASYNC 是 bit4", IOSQE_ASYNC == 1 << 4 and IOSQE_ASYNC_BIT == 4)
check("IOSQE_BUFFER_SELECT 是 bit5", IOSQE_BUFFER_SELECT == 1 << 5)
check("IOSQE_CQE_SKIP_SUCCESS 是 bit6", IOSQE_CQE_SKIP_SUCCESS == 1 << 6)
check("四个 SQE flag 互不重叠",
      IOSQE_FIXED_FILE | IOSQE_IO_LINK | IOSQE_ASYNC | IOSQE_BUFFER_SELECT ==
      1 | 4 | 16 | 32)

print("== 4. opcode 与文件 IO ==")
check("IORING_OP_READ == 22", IORING_OP_READ == 22)
check("IORING_OP_READ_FIXED == 4", IORING_OP_READ_FIXED == 4)
check("IORING_OP_WRITE_FIXED == 5", IORING_OP_WRITE_FIXED == 5)
check("IORING_OP_READV_FIXED == 62", IORING_OP_READV_FIXED == 62)
check("IORING_OP_FTRUNCATE == 56", IORING_OP_FTRUNCATE == 56)
check("FIXED 版比普通 READ 晚定义（4 早于 22 但语义是索引）",
      IORING_OP_READ_FIXED < IORING_OP_READ)
check("OP_NAME 覆盖用到的 opcode",
      OP_NAME[IORING_OP_READ_FIXED] == "READ_FIXED" and
      OP_NAME[IORING_OP_READ] == "READ")

print("== 5. 注册缓冲区：索引 vs 指针 ==")
rb = RegisteredBuffers()
check("注册 4 个缓冲区", rb.register([bytearray(4096) for _ in range(4)]) == 4)
try:
    rb.register([bytearray(16)])
    check("重复注册 → EBUSY", False)
except ValueError:
    check("重复注册 → EBUSY", True)
sq = prep_read(rb, fd=3, index=2, length=4096, off=0, user_data=42)
check("READ_FIXED 的 addr 是索引 2", sq.addr == 2)
check("READ_FIXED 的 buf_index 也是 2", sq.buf_index == 2)
check("READ_FIXED 命中 needs_buffer()", sq.needs_buffer())
sq2 = prep_read_plain(fd=3, addr=0x7F0000001234, length=4096, off=0,
                      user_data=43)
check("普通 READ 的 addr 是地址", sq2.addr == 0x7F0000001234)
check("普通 READ 不走固定缓冲区", not sq2.needs_buffer())
check("固定文件需要 IOSQE_FIXED_FILE（否则 fd 被当真实 fd）",
      not prep_read(rb, 3, 2, 4096, 0, 42).uses_fixed_file() and
      prep_read(rb, 3, 2, 4096, 0, 42, fixed_file=True).uses_fixed_file())
rb.update(1, bytearray(8192), tag=7)
check("UPDATE 能替换已有槽位", len(rb.get(1)) == 8192 and rb.tags[1] == 7)
try:
    rb.update(9, bytearray(16))
    check("UPDATE 越界 → EINVAL（不能扩容）", False)
except ValueError:
    check("UPDATE 越界 → EINVAL（不能扩容）", True)
check("注册常量取值", IORING_REGISTER_BUFFERS == 0 and
      IORING_UNREGISTER_BUFFERS == 1 and IORING_REGISTER_FILES == 2 and
      IORING_REGISTER_BUFFERS2 == 15 and IORING_REGISTER_BUFFERS_UPDATE == 16)

print("== 6. CQE：res 是 -errno 的单通道 ==")
c = CQE(42, -5)
check("res=-5 → errno 5(EIO)", c.errno == 5 and not c.ok())
c2 = CQE(42, 4096)
check("res=4096 → 成功，errno 0", c2.ok() and c2.errno == 0)
check("res=0 也算成功（EOF）", CQE(1, 0).ok())
packed = c.pack()
check("CQE 打包 16 字节且能解回来",
      len(packed) == 16 and CQE.unpack(packed).res == -5)
check("负 res 往返不变", CQE.unpack(CQE(7, -11).pack()).errno == 11)

print("== 7. 环：SQ 间接 / CQ 直接 ==")
ring = Ring(4)
check("cq_entries 默认 2 倍", ring.cq_entries == 8)
s = ring.get_sqe()
check("拿到 SQE 后 sq_tail 前进", ring.sq_tail == 1 and s is not None)
for _ in range(3):
    ring.get_sqe()
check("SQ 满后再取返回 None", ring.get_sqe() is None)
check("sq_pending == sq_entries", ring.sq_pending() == 4)
check("SQ 是间接的：array 里存的是下标",
      sorted(set(ring.sq_array)) == [0, 1, 2, 3])

ring2 = Ring(2)
ring2.fill_cq(CQE(1, 10))
ring2.fill_cq(CQE(2, 20))
check("CQ 可以装 2*entries=4 个", ring2.cq_pending() == 2)
got = ring2.reap()
check("收割 2 个且 user_data 保序", [g.user_data for g in got] == [1, 2])
check("收割后 CQ 空", ring2.cq_pending() == 0)

print("== 8. CQ 溢出（IORING_SETUP_CQ_OVERFLOW 语义对照） ==")
r_no = Ring(1)
r_no.fill_cq(CQE(1, 1))
r_no.fill_cq(CQE(2, 1))
ok = r_no.fill_cq(CQE(3, 1))
check("不允许溢出时第三个被丢弃", (not ok) and r_no.dropped == 1)
r_ov = Ring(1, cq_overflow=True)
for i in range(1, 4):
    r_ov.fill_cq(CQE(i, 1))
check("允许溢出时第三个进 overflow 队列",
      len(r_ov.overflow) == 1 and r_ov.overflow[0].user_data == 3)
got = r_ov.reap(2)
check("先收割 ring 里的 2 个", [g.user_data for g in got] == [1, 2])
check("腾出空间后 overflow 自动回填", r_ov.cq_pending() == 1)
got2 = r_ov.reap()
check("回填的那个能再被收割", [g.user_data for g in got2] == [3])

print("== 9. 是否需要系统调用 ==")
r_plain = Ring(8)
check("普通模式提交需要 io_uring_enter", r_plain.need_syscall())
check("等待完成时必然要进内核", r_plain.need_syscall(min_complete=1))
r_poll = Ring(8, sqpoll=True)
check("SQPOLL 提交不要系统调用", not r_poll.need_syscall())
check("SQPOLL 等完成仍要 getevents", r_poll.need_syscall(min_complete=1))

print("== 10. mmap 长度 ==")
check("SQ ring = 64 + entries*4", mmap_length("sq_ring", 8, 16) == 64 + 32)
check("SQEs = entries*64", mmap_length("sqes", 8, 16) == 8 * 64)
check("CQ ring = 64 + cq_entries*16", mmap_length("cq_ring", 8, 16) == 64 + 16 * 16)
check("CQE32 时 CQ ring 翻倍",
      mmap_length("cq_ring", 8, 16, cqe_size=CQE_SIZE_32) == 64 + 16 * 32)
check("SQE128 时 SQEs 翻倍",
      mmap_length("sqes", 8, 16, sqe_size=SQE_SIZE_128) == 8 * 128)

print()
print("TOTAL: %d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
