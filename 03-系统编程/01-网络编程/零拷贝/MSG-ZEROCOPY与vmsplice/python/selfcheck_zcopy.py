"""自检:MSG_ZEROCOPY / vmsplice 行为模型。纯计算断言。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zcopy_model import (                                 # noqa: E402
    ZerocopySocket, Range, IOV_MAX, PAGE_SIZE,
    SO_EE_ORIGIN_ZEROCOPY, SO_EE_CODE_ZEROCOPY_COPIED, UINT32_MAX,
    ENOBUFS, EBADF, EINVAL, EAGAIN,
    SPLICE_F_GIFT, SPLICE_F_MOVE, SPLICE_F_MORE, SPLICE_F_NONBLOCK,
    vmsplice, vmsplice_direction, zerocopy_is_worth_it,
)

N = 0


def ok(cond, msg):
    global N
    N += 1
    if not cond:
        raise AssertionError("FAIL #%d: %s" % (N, msg))


def eq(got, want, msg):
    ok(got == want, "%s -> got %r want %r" % (msg, got, want))


# ---------------------------------------------- 必须先 setsockopt 声明意图
s = ZerocopySocket()
ret, ctr = s.send(4096)
eq((ret, ctr), (4096, None), "未开 SO_ZEROCOPY:内核静默忽略 flag,不发通知")
s.setsockopt_zerocopy(1)
ret, ctr = s.send(4096)
eq((ret, ctr), (4096, 1), "开之后才分配 counter")
ok(s.zc_enabled, "setsockopt(1) 生效")

# 开了 SO_ZEROCOPY 也可以混合使用:不带 flag 的调用不走零拷贝
s2 = ZerocopySocket(zc_enabled=True)
ret, ctr = s2.send(4096, zerocopy=False)
eq((ret, ctr), (4096, None), "带 SO_ZEROCOPY 但不带 MSG_ZEROCOPY -> 普通拷贝")

# ------------------------------------------------------- counter 的三条规则
s3 = ZerocopySocket(zc_enabled=True)
eq(s3.send(100000)[1], 1, "counter 按**调用**计数,不按字节")
eq(s3.send(1)[1], 2, "1 字节与 100 KB 一样只加 1")
eq(s3.send(0)[1], None, "length == 0 不增 counter")
eq(s3.send(0)[0], 0, "length == 0 返回 0")
ret, ctr = s3.send(4096, enobufs=True)
eq((ret, ctr), (-ENOBUFS, None), "失败(ENOBUFS)不增 counter")
eq(s3.send(4096)[1], 3, "失败之后的下一次成功接着数到 3")

# ------------------------------------------------------------- u32 回绕
s4 = ZerocopySocket(zc_enabled=True)
s4.counter = UINT32_MAX - 1
eq(s4.send(10)[1], UINT32_MAX, "递增到 UINT_MAX")
eq(s4.send(10)[1], 0, "再增一次回绕到 0")
eq(s4.send(10)[1], 1, "继续递增")

# ------------------------------------------------------------ 通知合并
s5 = ZerocopySocket(zc_enabled=True)
ok(s5.complete(1) is True, "第一条通知:新起一个包")
eq(s5.outstanding(), 1, "队列长度 1")
ok(s5.complete(2) is False, "第二条恰好接续上界 -> 被合并")
eq(s5.outstanding(), 1, "合并后仍只有 1 个 outstanding")
notif = s5.recv_errqueue()
eq(notif["ee_info"], 1, "ee_info = 区间下界")
eq(notif["ee_data"], 2, "ee_data = 区间上界(闭区间)")
eq(notif["ee_origin"], SO_EE_ORIGIN_ZEROCOPY, "ee_origin = 5")
eq(notif["ee_errno"], 0, "ee_errno 恒 0(避免阻塞 send/recv)")
eq(notif["ee_code"], 0, "无拷贝 -> ee_code 0")
eq(s5.outstanding(), 0, "取走后队列空")
eq(s5.recv_errqueue(), None, "空队列返回 None")

# 乱序:接不上就另起一个包
s6 = ZerocopySocket(zc_enabled=True)
s6.complete(1)
ok(s6.complete(4) is True, "跳号 -> 不合并")
eq(s6.outstanding(), 2, "两条独立通知")
eq(s6.recv_errqueue()["ee_data"], 1, "先到的是 [1,1]")
eq(s6.recv_errqueue()["ee_data"], 4, "后到的是 [4,4]")

# 回绕处也算「接续」
s7 = ZerocopySocket(zc_enabled=True)
s7.complete(UINT32_MAX)
ok(s7.complete(0) is False, "UINT_MAX -> 0 在 u32 意义上是连续的,仍然合并")
eq(s7.outstanding(), 1, "回绕不产生新包")

# ------------------------------------------------------------ 拷贝退化
s8 = ZerocopySocket(zc_enabled=True, loopback=True)
s8.complete(1)
n = s8.recv_errqueue()
eq(n["ee_code"], SO_EE_CODE_ZEROCOPY_COPIED, "loopback 必定走 deferred copy")
s9 = ZerocopySocket(zc_enabled=True)
s9.complete(1, copied=True)
eq(s9.recv_errqueue()["ee_code"], SO_EE_CODE_ZEROCOPY_COPIED, "显式 copied -> ee_code=1")
s10 = ZerocopySocket(zc_enabled=True, loopback=True)
s10.complete(1, copied=False)
eq(s10.recv_errqueue()["ee_code"], 0, "loopback=True 但显式覆盖成不拷贝 -> 0")

# 负控:完成通知不等于传输完成
s11 = ZerocopySocket(zc_enabled=True)
s11.send(4096)
s11.complete(1, copied=True)
eq(s11.recv_errqueue()["ee_code"], 1,
   "通知到了但数据可能还没传完(deferred copy 语义)")

# --------------------------------------------------------- 量级建议阈值
ok(zerocopy_is_worth_it(64 * 1024) is True, "64 KiB 值得开")
ok(zerocopy_is_worth_it(1 * 1024) is False, "1 KiB 不值得")
ok(zerocopy_is_worth_it(10 * 1024) is False, "恰好 10 KiB 属于「约 10 KB」以下")
eq(IOV_MAX, 1024, "IOV_MAX")
eq(PAGE_SIZE, 4096, "页大小")

# ------------------------------------------------------------- vmsplice
eq(vmsplice(False, [(0, 4096)], 0), (-1, EBADF), "fd 不是 pipe -> EBADF")
eq(vmsplice(True, [(0, 1)] * (IOV_MAX + 1), 0), (-1, EINVAL), "nr_segs > IOV_MAX")
eq(vmsplice(True, [(0, 1)] * IOV_MAX, 0), (IOV_MAX, 0), "恰好 IOV_MAX 段放行")
eq(vmsplice(True, [(PAGE_SIZE, PAGE_SIZE)], SPLICE_F_GIFT), (PAGE_SIZE, 0),
   "GIFT + 页对齐 -> 成功")
eq(vmsplice(True, [(PAGE_SIZE + 1, PAGE_SIZE)], SPLICE_F_GIFT), (-1, EINVAL),
   "GIFT 但基址未对齐 -> EINVAL")
eq(vmsplice(True, [(PAGE_SIZE, PAGE_SIZE - 1)], SPLICE_F_GIFT), (-1, EINVAL),
   "GIFT 但长度未对齐 -> EINVAL")
eq(vmsplice(True, [(PAGE_SIZE + 1, 7)], 0), (7, 0),
   "不带 GIFT 时不要求对齐(成对用例)")
eq(vmsplice(True, [(0, 8)], SPLICE_F_MOVE), (8, 0),
   "SPLICE_F_MOVE 对 vmsplice 未使用,不影响结果")
eq(vmsplice(True, [(0, 8)], SPLICE_F_MORE), (8, 0), "SPLICE_F_MORE 当前无效果")
eq(vmsplice(True, [(0, 8)], SPLICE_F_NONBLOCK), (8, 0), "非阻塞且可立即完成")
eq(vmsplice(True, [(0, 8)], SPLICE_F_NONBLOCK, would_block=True), (-1, EAGAIN),
   "非阻塞且会阻塞 -> EAGAIN")
eq(vmsplice(True, [(0, 8), (8, 8)], 0), (16, 0), "多段累加")
eq(vmsplice(True, [(0, 0)], 0), (0, 0), "零长度段返回 0")

eq(vmsplice_direction(True), "splice", "写端:真把用户页映射进管道")
eq(vmsplice_direction(False), "copy", "读端:实际上只是拷贝")

# Range 自身
_r = Range(3, 5)
eq(_r.as_extended_err()["ee_info"], 3, "Range ee_info")
eq(_r.as_extended_err()["ee_data"], 5, "Range ee_data")
eq(_r.as_extended_err()["ee_code"], 0, "Range 默认不拷贝")

print("selfcheck OK: %d assertions" % N)
