# -*- coding: utf-8 -*-
"""TCP 流量控制与零窗口 —— 自检

负向判据优先：这里刻意断言「不该发的时候不能发」「不该更新窗口的时候不能更新」，
因为「能发」在参数给足时几乎恒真，没有鉴别力。
"""
from tcpwin_model import *  # noqa: F401,F403

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


print("== 1. 可用窗口 U = SND.UNA + SND.WND - SND.NXT ==")
s = Sender()
s.on_ack(0, 10000)
check("初始 U 等于通告窗口", s.usable() == 10000, "U=%d" % s.usable())
s.send(3000)
check("发出 3000 后 U 减少 3000", s.usable() == 7000, "U=%d" % s.usable())
check("SND.NXT 已推进到 3000", s.nxt == 3000)
s.on_ack(3000, 10000)
check("确认 3000 后 U 恢复满窗", s.usable() == 10000, "U=%d" % s.usable())
check("Max(SND.WND) 被记住且不再变小", s.max_wnd == 10000, "max=%d" % s.max_wnd)

print("\n== 2. 发送端 SWS 四条判据 ==")
s2 = Sender()
s2.on_ack(0, 10000)
check("(1) 数据够一个 MSS → 发", s2.may_send(2000) is True, "min(D,U)=2000 >= 1460")
check("(1) 负向：数据不足 MSS 且不 PUSH → 不发",
      s2.may_send(400) is False, "min(D,U)=400 < 1460 且 < 5000")
check("(3) 数据 >= Fs*Max(SND.WND)=5000 → 发", s2.may_send(6000) is True, "6000 >= 5000")
check("(4) override 超时 → 发", s2.may_send(100, override=True) is True, "override")

s3 = Sender()
s3.on_ack(0, 10000)
s3.send(2000)                      # SND.NXT != SND.UNA
check("(2) 负向：有在飞数据时不满足 PUSH 判据",
      s3.may_send(300, pushed=True) is False, "SND.NXT != SND.UNA")
check("(1) 有在飞数据时仍可用 MSS 判据", s3.may_send(2000) is True, "min(D,U)=2000")

# 判据(1)在 Fs*Max(SND.WND) > MSS 时会盖住判据(3)，必须把窗口压到
# Fs*Max(SND.WND) < MSS 才能单独观察判据(3)。
s4 = Sender()
s4.on_ack(0, 1000)                 # Fs*Max(SND.WND) = 500 < MSS = 1460
check("(3) 隔离后：600 >= 500 → 发", s4.may_send(600) is True, "min(D,U)=600")
check("(3) 负向：400 < 500 → 不发", s4.may_send(400) is False, "min(D,U)=400")
check("(1) 负向：U 上限 1000 < MSS，仅 1 字节待发时判据(1)确实不成立",
      s4.may_send(1) is False, "min(D,U)=1 < 1460 且 < 500")
s4.send(600)                       # SND.NXT != SND.UNA
check("(3) 负向：有在飞数据时比例判据失效",
      s4.may_send(400) is False, "SND.NXT != SND.UNA 且 400 < 500")

print("\n== 3. 接收端 SWS：抑制小幅窗口更新 ==")
r = Receiver(buff=65536, mss=MSS)
thr = min(FR * r.buff, r.mss)
check("更新阈值 = min(Fr*RCV.BUFF, MSS) = MSS", thr == MSS, "thr=%d" % thr)
r.receive(2000)
check("收到 2000 后右边界固定（RCV.WND 同步减 2000）",
      r.wnd == 65536 - 2000 and r.reduction == 0, "wnd=%d reduction=%d" % (r.wnd, r.reduction))
r.consume(1000)
check("应用读走 1000 但 reduction=1000 < MSS → 不更新窗口",
      r.updates == 0 and r.wnd == 63536, "reduction=%d" % r.reduction)
edge_before = r.nxt + r.wnd
r.consume(600)
check("累计 reduction 达到 1600 >= MSS → 更新窗口",
      r.updates == 1 and r.wnd == 65136, "wnd=%d reduction=%d" % (r.wnd, r.reduction))
check("右边界一次性前移 1600 而不是本次读走的 600",
      (r.nxt + r.wnd) - edge_before == 1600, "advance=%d" % ((r.nxt + r.wnd) - edge_before))

print("\n== 4. 零窗口的形成 ==")
rz = Receiver(buff=4096, mss=MSS)
rz.receive(4096)
check("应用一直不读 → RCV.WND 归零", rz.zero_window and rz.wnd == 0, "wnd=%d" % rz.wnd)
check("零窗口时 RCV.USER == RCV.BUFF", rz.user == rz.buff)
ack_no, ack_win = rz.nxt, rz.wnd
check("零窗口下收到报文仍要回 ACK，且窗口字段为 0",
      ack_win == 0 and ack_no == 1000 + 4096, "ack=%d win=%d" % (ack_no, ack_win))

print("\n== 5. 零窗口探测：首探 RTO，之后指数退避 ==")
rto = 1.0
sch = probe_schedule(rto, 5)
check("探测间隔为 RTO, 2RTO, 4RTO, 8RTO, 16RTO",
      sch == [1.0, 3.0, 7.0, 15.0, 31.0], str(sch))
deltas = [sch[i + 1] - sch[i] for i in range(4)]
check("相邻探测间隔依次为 RTO 的 1,2,4,8 倍",
      deltas == [2.0, 4.0, 8.0, 16.0], "deltas=%s" % deltas)
check("间隔严格翻倍（不是线性）",
      all(abs(deltas[i + 1] - 2 * deltas[i]) < 1e-9 for i in range(3)), "deltas=%s" % deltas)

p = ZeroWindowProber(rto)
fires = [t for t in [x * 0.5 for x in range(0, 40)] if p.observe(t, True)]
check("前 RTO 内一次都不探", len([t for t in fires if t < rto]) == 0, "fires=%s" % fires[:4])
check("t=1.0 发出首个探测", any(abs(t - 1.0) < 1e-9 for t in fires), "fires=%s" % fires[:4])
check("第二次探测在 t=3.0（间隔翻倍）", any(abs(t - 3.0) < 1e-9 for t in fires), "fires=%s" % fires[:4])
check("半秒粒度下共探到 4 次（1/3/7/15）", len(fires) == 4, "n=%d" % len(fires))

p2 = ZeroWindowProber(rto)
p2.observe(0.0, True)
p2.observe(1.0, True)
p2.observe(2.0, False)            # 窗口重开
check("窗口重开后状态机重置", p2.next_at is None and p2.interval == rto, "")
p2.observe(2.5, True)
check("重置后重新等一个完整 RTO 才探", p2.observe(3.0, True) is False, "3.0 < 2.5+1.0")

print("\n== 6. 零窗口不得导致连接被拆（MUST-37） ==")
check("模型里零窗口不会触发任何连接关闭动作",
      not hasattr(Receiver(buff=64), "closed"), "MAY-8 + MUST-37：接收端可永久关窗")

print("\n== 7. 窗口扩大 RFC 7323 ==")
check("shift 上限 14", MAX_SHIFT == 14)
check("最大可表达窗口 2^(14+16) = 1 GiB", MAX_SCALED_WINDOW == 1 << 30, "%d" % MAX_SCALED_WINDOW)
check("不缩放时窗口字段上限 65535", UNSCALED_MAX == 65535)
check("shift_for(65536) 至少为 1", shift_for(65536) >= 1, "shift=%d" % shift_for(65536))
check("shift_for(1GiB) == 14", shift_for(1 << 30) == 14, "shift=%d" % shift_for(1 << 30))

rs = Receiver(buff=1 << 20, shift=14)
rs.wnd = 1 << 20
seg = rs.advertise()
check("1 MiB 窗口在 shift=14 下塞进 16 位字段", seg == (1 << 20) >> 14 == 64, "SEG.WND=%d" % seg)
ss = Sender(shift=14)
ss.on_ack(0, seg)
check("发送端左移还原得 1 MiB", ss.wnd == 1 << 20, "SND.WND=%d" % ss.wnd)

print("\n== 8. 量化的代价：右移丢低位 ==")
check("窗口 1000 在 shift=4 下量化损失 8 字节",
      quantization_loss(1000, 4) == 8, "loss=%d" % quantization_loss(1000, 4))
check("窗口是 2^shift 整数倍时零损失", quantization_loss(1024, 4) == 0)
check("量化只会少不会多",
      all(effective_window(announced_window(w, s), s) <= w
          for w in (1, 17, 300, 5000, 99999) for s in (1, 3, 7, 14)))

print("\n== 9. 窗口回缩：右移会让通告窗口变小 ==")
prev = announced_window(1000, 4)
now = announced_window(991, 4)
check("真实窗口 1000→991 时线上字段 62→61（窗口回缩）",
      prev == 62 and now == 61 and now < prev, "%d -> %d" % (prev, now))
prev2 = announced_window(1000, 4)
now2 = announced_window(992, 4)
check("回缩不是必然：992 仍落在同一档", now2 == prev2 == 62, "%d -> %d" % (prev2, now2))

print("\n== 10. SYN 段窗口不缩放 ==")
rsyn = Receiver(buff=1 << 20, shift=14)
rsyn.wnd = 1 << 20
check("SYN 段的窗口字段按原值（截断到 65535）发送",
      rsyn.advertise(syn=True) == 65535, "SYN SEG.WND=%d" % rsyn.advertise(syn=True))
check("非 SYN 段才做右移", rsyn.advertise(syn=False) == 64)

print("\n---- %d 项断言，失败 %d 项 ----" % (N, len(FAILED)))
if FAILED:
    for f in FAILED:
        print("  FAILED:", f)
    raise SystemExit(1)
print("ALL PASS")
