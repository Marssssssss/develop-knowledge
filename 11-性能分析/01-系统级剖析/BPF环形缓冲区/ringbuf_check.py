"""BPF ring buffer —— 自检（实跑）。锚点来自 docs.kernel.org/bpf/ringbuf.html。"""

from ringbuf import (
    BUSY_BIT,
    DISCARD_BIT,
    HDR_SIZE,
    RB_AVAIL_DATA,
    RB_CONS_POS,
    RB_FORCE_WAKEUP,
    RB_NO_WAKEUP,
    RB_PROD_POS,
    RB_RING_SIZE,
    RingBuf,
    align8,
    is_pow2,
)

_passed = 0
_failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"ok   {label} {detail}")
    else:
        _failed += 1
        print(f"FAIL {label} {detail}")


# --- 1. 基本约束 --------------------------------------------------------------
check("2 的幂判定", is_pow2(4096) and is_pow2(1 << 20) and not is_pow2(3000), "")
try:
    RingBuf(3000)
    check("非 2 的幂被拒", False, "应当抛错")
except ValueError:
    check("非 2 的幂被拒", True, "max_entries 必须是 2 的幂")
check("记录按 8 字节对齐", align8(8 + 1) == 16 and align8(8 + 8) == 16 and align8(8 + 9) == 24,
      f"{align8(9)} / {align8(16)} / {align8(17)}")

rb = RingBuf(4096)
check("初始位置为 0", rb.query(RB_CONS_POS) == 0 and rb.query(RB_PROD_POS) == 0, "")
check("RING_SIZE = max_entries", rb.query(RB_RING_SIZE) == 4096, "")

# --- 2. reserve / commit ------------------------------------------------------
p0 = rb.reserve(16)
check("reserve 成功返回指针", p0 == 0, f"{p0}")
check("reserve 立即推进 producer", rb.query(RB_PROD_POS) == align8(HDR_SIZE + 16),
      f"prod={rb.query(RB_PROD_POS)}")
check("未提交前不可用", rb.avail_data() == 0 and rb.drain() == [], "busy 位未清")
length, flags, pg = rb._get_hdr(p0)
check("头部 8 字节里有长度", length == 16, f"{length}")
check("reserve 时置 busy 位", bool(flags & BUSY_BIT), f"{flags:#x}")
check("头部带页偏移", pg == 0, f"pg_off={pg}")

rb.write_payload(p0, b"hello")
check("commit 成功", rb.commit(p0) is True, "")
_, flags2, _ = rb._get_hdr(p0)
check("commit 后清 busy 位", not flags2 & BUSY_BIT, f"{flags2:#x}")
check("commit 后可消费", rb.avail_data() > 0, f"{rb.avail_data()}")

# --- 3. 慢生产者挡住后面已提交的记录 ------------------------------------------
rb2 = RingBuf(4096)
a = rb2.reserve(16)          # 先保留
b = rb2.reserve(16)          # 后保留
rb2.commit(b)                # 后保留的**先**提交
check("后保留的先提交也不可见", rb2.avail_data() == 0, "前面的 a 还处于 busy")
rb2.commit(a)                # 前一条才提交
check("前一条提交后两条一起可见", rb2.avail_data() == 2 * align8(HDR_SIZE + 16),
      f"{rb2.avail_data()}")
got = rb2.drain()
check("按保留顺序交付", [r.pos for r in got] == [a, b], f"{[r.pos for r in got]}")

# --- 4. discard ---------------------------------------------------------------
rb3 = RingBuf(4096)
d1 = rb3.reserve(8)
d2 = rb3.reserve(8)
rb3.discard(d1)              # all-or-nothing 里第一条作废
rb3.commit(d2)
_, fl, _ = rb3._get_hdr(d1)
check("discard 置 discard 位", bool(fl & DISCARD_BIT), f"{fl:#x}")
drained = rb3.drain()
check("被 discard 的记录仍会被消费（跳过）", len(drained) == 2, f"{len(drained)}")
check("状态标记为 discarded", drained[0].state == "discarded", drained[0].state)
check("消费后位置推进到 prod", rb3.query(RB_CONS_POS) == rb3.query(RB_PROD_POS), "")

# --- 5. 空间不足不阻塞 + output 变体 ------------------------------------------
rb4 = RingBuf(64)
ok_out = rb4.output(b"x" * 24)
check("output 走 reserve+commit", ok_out and rb4.avail_data() > 0, f"{rb4.avail_data()}")
full = None
for i in range(10):
    if rb4.reserve(24) is None:
        full = i
        break
check("填满后 reserve 返回 None（不阻塞）", full is not None, f"第 {full} 次失败")
check("失败时 producer 不再推进", rb4.query(RB_PROD_POS) <= rb4.size,
      f"prod={rb4.query(RB_PROD_POS)} size={rb4.size}")
rb5 = RingBuf(64)
ptr = rb5.reserve(8)
check("重复 commit 同一指针失败", rb5.commit(ptr) and not rb5.commit(ptr), "只能提交一次")

# --- 6. NMI：拿不到自旋锁 ------------------------------------------------------
rb6 = RingBuf(256)
rb6.lock_held = True
check("NMI 下 reserve 失败（即使没满）", rb6.reserve(8, nmi=True) is None, "")
rb6.lock_held = False
check("锁释放后恢复正常", rb6.reserve(8) is not None, "")

# --- 7. 自节流通知 ------------------------------------------------------------
rb7 = RingBuf(1024)
n0 = rb7.notifications
q1 = rb7.reserve(8)
rb7.commit(q1)               # 消费者已在 0 ⇒ 追上了 ⇒ 通知
check("消费者已追上 ⇒ 通知", rb7.notifications == n0 + 1, f"{rb7.notifications}")
q2 = rb7.reserve(8)
rb7.commit(q2)               # 消费者还在 0，没追到 q2 ⇒ 不通知
check("消费者落后 ⇒ 不发通知（自节流）", rb7.notifications == n0 + 1, f"{rb7.notifications}")
q3 = rb7.reserve(8)
rb7.commit(q3, flags=RB_FORCE_WAKEUP)
check("FORCE_WAKEUP 强制通知", rb7.notifications == n0 + 2, f"{rb7.notifications}")
q4 = rb7.reserve(8)
rb7.commit(q4, flags=RB_NO_WAKEUP)
check("NO_WAKEUP 抑制通知", rb7.notifications == n0 + 2, f"{rb7.notifications}")

# --- 8. 双映射绕回 ------------------------------------------------------------
rb8 = RingBuf(64)
check("跨绕回读取仍连续", rb8.read_linear(60, 8) == bytes(8), "虚拟地址上两段接在一起")

# --- 9. 查询值是快照 ----------------------------------------------------------
rb9 = RingBuf(128)
r = rb9.reserve(8)
check("PROD_POS 含未提交部分", rb9.query(RB_PROD_POS) == align8(HDR_SIZE + 8), "")
check("AVAIL_DATA 不含未提交部分", rb9.query(RB_AVAIL_DATA) == 0, "")
rb9.commit(r)
check("提交后 AVAIL_DATA 才出现", rb9.query(RB_AVAIL_DATA) == align8(HDR_SIZE + 8), "")

print(f"\n{_passed} passed, {_failed} failed")
raise SystemExit(1 if _failed else 0)
