"""自检:TCP_QUICKACK / TCP_USER_TIMEOUT 模型。全部为纯计算断言,无需内核。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dack_model import (                                  # noqa: E402
    DelackEngine, TCP_ATO_MIN, TCP_DELACK_MAX, TCP_DELACK_MIN, TCP_RTO_MIN,
    TCP_RTO_MAX, TCP_TIMEOUT_MIN, TCP_MAX_QUICKACKS, usec_to_ms_ceil,
    clamp_rto_to_user_timeout, clamp_probe0_to_user_timeout, ilog2,
    model_timeout, retransmits_timed_out, probe_timer_decision,
)

N = 0


def ok(cond, msg):
    global N
    N += 1
    if not cond:
        raise AssertionError("FAIL #%d: %s" % (N, msg))


def eq(got, want, msg):
    ok(got == want, "%s -> got %r want %r" % (msg, got, want))


# ---------------------------------------------------------------- 常量与换算
eq(TCP_ATO_MIN, 40, "TCP_ATO_MIN = HZ/25 = 40ms(HZ=1000)")
eq(TCP_DELACK_MIN, 40, "TCP_DELACK_MIN = HZ/25 = 40ms")
eq(TCP_DELACK_MAX, 200, "TCP_DELACK_MAX = HZ/5 = 200ms")
eq(TCP_RTO_MIN, 200, "TCP_RTO_MIN = HZ/5")
eq(TCP_RTO_MAX, 120000, "TCP_RTO_MAX = 120s")
eq(TCP_TIMEOUT_MIN, 2, "TCP_TIMEOUT_MIN")
eq(TCP_MAX_QUICKACKS, 16, "TCP_MAX_QUICKACKS")
eq(usec_to_ms_ceil(0), 0, "0us")
eq(usec_to_ms_ceil(1), 1, "1us 向上取整")
eq(usec_to_ms_ceil(1000), 1, "1000us")
eq(usec_to_ms_ceil(1001), 2, "1001us 向上取整")
eq(ilog2(1), 0, "ilog2(1)")
eq(ilog2(600), 9, "ilog2(RTO_MAX/RTO_MIN)=ilog2(600)=9 线性退避阈值")
eq(ilog2(1024), 10, "ilog2(1024)")
eq(ilog2(1023), 9, "ilog2(1023)")

# ------------------------------------------------------- tcp_incr_quickack
eq(DelackEngine(1460, 65536).incr_quickack(16), 16, "65536//2920=22 被 16 封顶")
eq(DelackEngine(1460, 65536).incr_quickack(2), 2, "max_quickacks=2 生效")
eq(DelackEngine(1460, 1460).incr_quickack(16), 2, "商为 0 时兜底成 2")
eq(DelackEngine(1460, 1460 * 6).incr_quickack(16), 3, "8760//2920 = 3")
_m = DelackEngine(1460, 65536)
_m.incr_quickack(16)
eq(_m.incr_quickack(2), 16, "incr_quickack 只增不减")
eq(DelackEngine(1460, 0).incr_quickack(16), 2, "窗口 0 -> 兜底 2(非 0)")

# ------------------------------------------------------- quickack 模式判定
_q = DelackEngine()
_q.quick = 1
_q.pingpong = False
ok(_q.in_quickack_mode() is True, "quick=1 且非 pingpong -> 快速确认")
_q.pingpong = True
ok(_q.in_quickack_mode() is False, "只翻 pingpong -> 关闭(成对用例)")
_q.pingpong = False
_q.quick = 0
ok(_q.in_quickack_mode() is False, "quick=0 且 dst=0 -> False(负控)")
_q.dst_quick_ack = True
ok(_q.in_quickack_mode() is True, "dst_quick_ack 短路,即使 quick=0")
_p = DelackEngine()
_p.pingpong = True
_p.enter_quickack_mode(16)
eq(_p.pingpong, False, "enter_quickack_mode 顺带清 pingpong")
eq(_p.ato, TCP_ATO_MIN, "enter_quickack_mode 把 ato 压到 TCP_ATO_MIN=40")
eq(_p.quick, 16, "enter_quickack_mode 抬 quick")

# ------------------------------------------------------------ ato 自适应
_a = DelackEngine(1460, 65536)
seq = []
for t in (0, 1, 3, 8, 20, 41, 71, 101, 201, 500):
    seq.append(_a.on_data_recv(t))
eq(seq, [40, 40, 40, 40, 40, 41, 50, 55, 55, 55],
   "到达时刻 0/1/3/8/20/41/71/101/201/500 的 ato 演化")
eq(_a.quick, 16, "整段里 quick 始终 16")
# ato 增长的不动点:a = floor(a/2) + m,整数解是 2m-1 而不是 2m
_b = DelackEngine(1460, 65536)
_b.on_data_recv(0)
_b.ato = 100
seen = []
for _ in range(12):
    _b.lrcvtime = 0
    _b.ato = _b.ato          # 保持
    _b.on_data_recv(99)
    seen.append(_b.ato)
eq(seen[-1], 197, "m=99 时 ato 收敛到 197 = 2m-1")
ok(200 not in seen, "ato 靠自身增长**到不了** TCP_DELACK_MAX=200")

# m > RTO 的分支:重新抬 quick 但不改 ato
_c = DelackEngine(1460, 65536)
_c.on_data_recv(0)
_c.quick = 0
_c.ato = 40
_c.on_data_recv(1000)
eq(_c.quick, 16, "间隔 1000ms > RTO 200ms -> 重新 incr_quickack")
eq(_c.ato, 40, "该分支不动 ato")
_c.quick = 0
_c.on_data_recv(1050)        # m = 50, 不 > rto, 也不 < ato(40) -> 空档
eq(_c.quick, 0, "m 落在空档(既不小于 ato 也不大于 rto)时什么都不做")

# ------------------------------------------------------ delack 定时器取值
_d = DelackEngine()
_d.ato = 40
eq(_d.delack_timeout(), 40, "ato == TCP_DELACK_MIN 时整段 if 跳过")
_d.ato = 41
eq(_d.delack_timeout(), 41, "ato=41 进慢路径,无 srtt 上限 500 -> 41")
_d.ato = 300
eq(_d.delack_timeout(), 200, "ato=300 被 tcp_delack_max() 压到 200")
_d.ato = 41
_d.srtt_us = 8000            # srtt=8ms -> srtt>>3 = 1ms
eq(_d.delack_timeout(), 40, "rtt 上限 1ms 被 TCP_DELACK_MIN=40 顶回 -> 40")
_d.srtt_us = 400000          # srtt=400ms -> srtt>>3 = 50ms
eq(_d.delack_timeout(), 41, "rtt 上限 50ms 松于 ato=41 -> 仍 41")
# pingpong / srtt 的差异会被最后那道 cap 抹平,只有不带 cap 才看得见
_u1 = DelackEngine(ato_of=0) if False else DelackEngine()
_u1.ato = 300
_u1.srtt_us = 2000000        # srtt=2s -> srtt>>3 = 250ms
eq(_u1.delack_timeout(apply_final_cap=False), 250, "非 pingpong:max_ato 被 srtt 压到 250")
_u1.pingpong = True
eq(_u1.delack_timeout(apply_final_cap=False), 200, "pingpong:max_ato 先被压到 200,srtt 反而失效")
_u2 = DelackEngine(delack_max=500)
_u2.ato = 300
eq(_u2.delack_timeout(), 300, "把 icsk_delack_max 放宽到 500 时 ato=300 不再被压")
_u2.pingpong = True
eq(_u2.delack_timeout(), 200, "同一配置下 pingpong 立刻把上限收回 200(成对用例)")

_r = DelackEngine()
eq(_r.delack_retry_delay(), (200, True), "retry=0 -> 200ms 且递增")
eq(_r.retry, 1, "retry 自增到 1")
_r.retry = 9
eq(_r.delack_retry_delay(), (102400, True), "200<<9 = 102400 < RTO_MAX -> 递增")
eq(_r.retry, 10, "retry 到 10")
eq(_r.delack_retry_delay(), (204800, False), "200<<10 = 204800 越过 RTO_MAX -> 不再递增")
eq(_r.retry, 10, "retry 停在 10")
eq(_r.ato, TCP_ATO_MIN, "重试分支把 ato 复位成 TCP_ATO_MIN")

# ------------------------------------------------- tcp_clamp_rto_to_user_timeout
eq(clamp_rto_to_user_timeout(0, 200, 5000), 200, "user_timeout=0 -> 直接用 rto")
eq(clamp_rto_to_user_timeout(10000, 200, 0), 200, "剩余充裕 -> rto")
eq(clamp_rto_to_user_timeout(10000, 200, 9900), 100, "剩余 100ms < rto -> 100")
eq(clamp_rto_to_user_timeout(10000, 200, 10000), 1, "恰好到点 -> 1 jiffy(不是 0)")
eq(clamp_rto_to_user_timeout(10000, 200, 99999), 1, "早已超时 -> 1")
eq(clamp_rto_to_user_timeout(1, 200, 0), 1, "user_timeout=1ms -> min(200,1)=1")

# -------------------------------------------- tcp_clamp_probe0_to_user_timeout
eq(clamp_probe0_to_user_timeout(0, 500, 1000, 11000), 500, "未设 user_timeout -> 原样")
eq(clamp_probe0_to_user_timeout(10000, 500, 0, 11000), 500, "probes_tstamp=0 -> 原样")
eq(clamp_probe0_to_user_timeout(10000, 9000, 1000, 2000), 9000, "剩余 9000 > when -> 取 when")
eq(clamp_probe0_to_user_timeout(10000, 500, 1000, 2000), 500, "when 更小 -> 取 when")
eq(clamp_probe0_to_user_timeout(10000, 9000, 1000, 11000), TCP_TIMEOUT_MIN,
   "剩余 0 被抬到 TCP_TIMEOUT_MIN=2")
eq(clamp_probe0_to_user_timeout(10000, 9000, 1000, 99999), TCP_TIMEOUT_MIN, "已超时同样抬到 2")
eq(clamp_probe0_to_user_timeout(10000, 9000, 1000, 500), 9000,
   "now < tstamp 被兜成 elapsed=0 -> 剩余 10000,取 when=9000")

# ------------------------------------------------------------ model_timeout
eq(model_timeout(0), 200, "boundary=0 -> (2<<0-1)*200 = 200")
eq(model_timeout(1), 600, "boundary=1 -> 3*200")
eq(model_timeout(9), 204600, "boundary=9 在线性退避阈值内 -> 1023*200")
eq(model_timeout(10), 324600, "boundary=10 越界 -> 204600 + 1*120000")
eq(model_timeout(15), 924600, "boundary=15(tcp_retries2 默认)-> 924.6s ≈ 15.4 分钟")

# ---------------------------------------------------- retransmits_timed_out
ok(retransmits_timed_out(15, 0, 0, 924600, 0) is False, "从未重传 -> 永不超时(负控)")
ok(retransmits_timed_out(15, 0, 0, 924599, 3) is False, "差 1ms 不算到点")
ok(retransmits_timed_out(15, 0, 0, 924600, 3) is True, "恰好到点(s32 比较 >=0)")
ok(retransmits_timed_out(15, 0, 100, 924700, 3) is True, "retrans_stamp 右移则判据右移")
ok(retransmits_timed_out(15, 0, 100, 924699, 3) is False, "边界对:差 1ms 翻面")
ok(retransmits_timed_out(15, 5000, 0, 4999, 1) is False, "自定义 timeout 生效")
ok(retransmits_timed_out(15, 5000, 0, 5000, 1) is True, "自定义 timeout 到点")
ok(retransmits_timed_out(1, 924600, 0, 924600, 1) is True, "给了 timeout 就忽略 boundary")

# ---------------------------------------------------- tcp_probe_timer 判定
eq(probe_timer_decision(0, 1000, 2000, 0, 15, 1, True), "reset_probes", "还有在飞数据")
eq(probe_timer_decision(0, 1000, 2000, 0, 15, 0, False), "reset_probes", "没有待发 skb")
eq(probe_timer_decision(0, 0, 2000, 0, 15, 0, True), "probe", "首次只打时间戳")
eq(probe_timer_decision(10000, 1000, 11000, 3, 15, 0, True), "abort", "user_timeout 到点")
eq(probe_timer_decision(10000, 1000, 10999, 3, 15, 0, True), "probe", "差 1ms 不 kill(成对)")
eq(probe_timer_decision(10000, 1000, 10999, 15, 15, 0, True), "abort", "次数满同样 abort")
eq(probe_timer_decision(10000, 1000, 10999, 14, 15, 0, True), "probe", "14 < 15 继续探")
eq(probe_timer_decision(0, 1000, 99999, 5, 15, 0, True), "probe",
   "未设 user_timeout:次数未满就永远探(RFC 1122 4.2.2.17)")

print("selfcheck OK: %d assertions" % N)
