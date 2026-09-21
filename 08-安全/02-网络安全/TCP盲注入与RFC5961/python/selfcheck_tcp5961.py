"""TCP 盲注入与 RFC 5961 自检（实跑）。"""

import random
import sys

from tcp5961 import (
    MASK32, INT_MAX, RESET_CHECK_STATES,
    TCP_ESTABLISHED, TCP_SYN_RECV, TCP_CLOSE_WAIT, TCP_LAST_ACK, TCP_CLOSING,
    Netns, TcpSock, validate_incoming, challenge_ack_allowed, oow_rate_limited,
    reset_check, tcp_sequence, ack_acceptable_rfc793, ack_acceptable_rfc5961,
    ack_window_size, sweep_tries, sweep_closed_form, mean_tries_continuous,
    seq_before, seq_after, to_signed32,
    PASS, RESET, DISCARD, CHALLENGE,
)

PASS_COUNT = 0


def ok(cond, msg):
    global PASS_COUNT
    assert cond, "ASSERT FAILED: " + msg
    PASS_COUNT += 1


def fixed_rand(value):
    return lambda lo, hi: min(max(value, lo), hi)


def seg(seq, rst=False, syn=False, ack=False, fin=False, ack_seq=0, payload=0):
    return {"seq": seq, "end_seq": (seq + payload + (1 if (syn or fin) else 0)) & MASK32,
            "rst": rst, "syn": syn, "ack": ack, "fin": fin, "ack_seq": ack_seq,
            "payload": payload}


def fresh(**kw):
    kw.setdefault("rcv_nxt", 1000)
    kw.setdefault("rcv_wnd", 32768)
    kw.setdefault("snd_una", 1000)
    kw.setdefault("snd_nxt", 1000)
    kw.setdefault("max_snd_wnd", 65536)
    return TcpSock(**kw)


NET = Netns(challenge_ack_limit=1000, invalid_ratelimit=500)
NOW = (1000, 100000)


def vi(sk, s, net=None, now=None):
    return validate_incoming(sk, s, net or Netns(), *(now or NOW), fixed_rand(1000))


def run(s, **kw):
    """每次用全新的 sock + netns，避免 per-socket/per-netns 限速串味。"""
    sacks = kw.pop("sacks", None)
    sk = fresh(**kw)
    if sacks is not None:
        sk.sacks = list(sacks)
    return validate_incoming(sk, s, Netns(), *NOW, fixed_rand(1000))


# ------------------------------------------------------------------ E1 RST

sk = fresh()
ok(run(seg(1000, rst=True))[0] == RESET, "E1a RST seq=RCV.NXT -> reset")
ok(run(seg(1000, rst=True))[1] == "seq_matches_rcv_nxt", "E1a 分支名")
ok(run(seg(1001, rst=True)) == (CHALLENGE, "rst_challenge"), "E1b 窗口内非精确 -> challenge ACK")
ok(run(seg(33768, rst=True)) == (CHALLENGE, "rst_challenge"),
   "E1c seq=RCV.NXT+RCV.WND 内核仍判窗口内(end_seq>limit 才是窗外)")
ok(run(seg(33769, rst=True))[0] == DISCARD, "E1d 严格超出右边界 -> 丢弃")
ok(run(seg(33769, rst=True))[1].startswith("silent/"), "E1d 窗外 RST 静默丢弃不发 challenge")
ok(run(seg(999, rst=True))[0] == DISCARD, "E1e seq=RCV.NXT-1 但 ESTABLISHED -> 不 reset")
ok(run(seg(999, rst=True), state=TCP_CLOSE_WAIT) == (RESET, "reset_check_out_of_window"),
   "E1f CLOSE_WAIT 下 seq=RCV.NXT-1 -> reset_check 放行")
# 同一个 sock 连续两次 challenge：第二次被 per-socket 限速吃掉
sk_dup = fresh()
ok(vi(sk_dup, seg(1500, rst=True)) == (CHALLENGE, "rst_challenge"), "E1g 首次 challenge 放行")
ok(vi(sk_dup, seg(1600, rst=True))[1] == "rst_challenge/oow_rate_limited",
   "E1g' 同一 jiffy 内的第二次被 per-socket 限速抑制")

# RFC 5961 §3.2 第 1 条与第 2 条在 SEQ==RCV.NXT 处重叠
def rfc5961_step1_outside(seq, rcv_nxt, rcv_wnd):
    return seq <= rcv_nxt or seq > rcv_nxt + rcv_wnd


def rfc793_acceptable(seq, rcv_nxt, rcv_wnd):
    return rcv_nxt <= seq < rcv_nxt + rcv_wnd


ok(rfc5961_step1_outside(1000, 1000, 32768) and rfc793_acceptable(1000, 1000, 32768),
   "E1g RFC5961 第1条(窗外)与第2条(可接受)在 SEQ==RCV.NXT 处同时为真 —— 文本不自洽")
ok(not (rfc5961_step1_outside(33768, 1000, 32768) and rfc793_acceptable(33768, 1000, 32768)),
   "E1g' 右端点处两条不重叠")
ok(run(seg(1000, rst=True))[0] == RESET,
   "E1h 实现以第3条『精确等于 RCV.NXT 就 reset』消解该重叠")

# ------------------------------------------------------------------ E2 SYN

ok(run(seg(1000, syn=True)) == (CHALLENGE, "syn_challenge"),
   "E2a SYN 即使 seq 恰好等于 RCV.NXT 也是 challenge 而非 reset")
ok(run(seg(99999, syn=True)) == (CHALLENGE, "syn_challenge"),
   "E2b SYN 在窗外也发 challenge（与 RST 的静默丢弃相反）")
sk2 = fresh(state=TCP_SYN_RECV, rcv_nxt=1000, snd_nxt=500)
s = seg(999, syn=True, ack=True, ack_seq=500, payload=0)
s["end_seq"] = 1000
ok(vi(sk2, s) == (PASS, "syn_recv_retransmitted_ack"),
   "E2c SYN_RECV 下重传的纯 ACK 走正常路径")

# ------------------------------------------------------------------ E3 reset_check 状态集合

for st in (TCP_CLOSE_WAIT, TCP_LAST_ACK, TCP_CLOSING):
    ok(run(seg(999, rst=True), state=st)[0] == RESET, "E3 状态 %d 在 reset_check 集合内" % st)
for st in (TCP_ESTABLISHED, TCP_SYN_RECV):
    ok(run(seg(999, rst=True), state=st)[0] != RESET, "E3 状态 %d 不在 reset_check 集合内" % st)
ok((1 << TCP_ESTABLISHED) & RESET_CHECK_STATES == 0, "E3 ESTABLISHED 位不在掩码中")

# ------------------------------------------------------------------ E4 SACK 右边缘

ok(run(seg(4000, rst=True), sacks=[(2000, 2500), (3000, 4000)]) == (RESET, "seq_matches_max_sack_edge"),
   "E4a RST seq == 最右 SACK 块的 end_seq -> reset")
ok(run(seg(2500, rst=True), sacks=[(2000, 2500), (3000, 4000)]) == (CHALLENGE, "rst_challenge"),
   "E4b RST seq == 非最右 SACK 块 end_seq -> 仍只发 challenge")
ok(run(seg(4001, rst=True), sacks=[(2000, 2500), (3000, 4000)]) == (CHALLENGE, "rst_challenge"),
   "E4c SACK 边缘 +1 -> challenge")

# ------------------------------------------------------------------ E5 challenge ACK 限速

net = Netns(challenge_ack_limit=1000)
n = 0
while challenge_ack_allowed(net, 1, fixed_rand(500)):
    n += 1
ok(n == 500, "E5a rand 取下界 half=500 时单秒允许 500 次（实测 %d）" % n)
net = Netns(challenge_ack_limit=1000)
n = 0
while challenge_ack_allowed(net, 1, fixed_rand(1499)):
    n += 1
ok(n == 1499, "E5b rand 取上界时单秒允许 1499 次 —— **超过 ack_limit**（实测 %d）" % n)
ok(1499 == 1000 + ((1000 + 1) >> 1) - 1, "E5b' 上界 = ack_limit + half - 1")
ok((1000 + 1) >> 1 == 500, "E5c half = (ack_limit+1)>>1 = 500")
net = Netns(challenge_ack_limit=INT_MAX)
net.tcp_challenge_count = 0
ok(challenge_ack_allowed(net, 1, fixed_rand(0)), "E5d ack_limit==INT_MAX 时恒允许（即使计数为 0）")
# 下界不是 0 而是 half：limit=4 -> half=2，单秒最少也能发 2 次
net = Netns(challenge_ack_limit=4)
n = 0
while challenge_ack_allowed(net, 1, fixed_rand(2)):
    n += 1
ok(n == 2, "E5e limit=4 时单秒最少允许 half=2 次（实测 %d）—— 计数复位下界不是 0" % n)
net = Netns(challenge_ack_limit=4)
n = 0
while challenge_ack_allowed(net, 1, fixed_rand(5)):
    n += 1
ok(n == 5, "E5f limit=4 时单秒最多允许 limit+half-1=5 次（实测 %d）" % n)
net = Netns(challenge_ack_limit=4)
ok(challenge_ack_allowed(net, 1, fixed_rand(2)) and challenge_ack_allowed(net, 1, fixed_rand(2)),
   "E5g 同一秒内第二次调用不再重随机")
ok(challenge_ack_allowed(net, 2, fixed_rand(5)) is True, "E5h 跨秒后重新随机")

# ------------------------------------------------------------------ E6 oow 限速

net = Netns(invalid_ratelimit=500)
ok(oow_rate_limited(net, 1000, 0) == (False, 1000), "E6a last=0 -> 不限流并打时间戳")
ok(oow_rate_limited(net, 1499, 1000) == (True, 1000), "E6b elapsed=499 < 500 -> 限流且不更新时间戳")
ok(oow_rate_limited(net, 1500, 1000) == (False, 1500), "E6c elapsed=500 -> 判据是严格小于故放行")
ok(oow_rate_limited(net, 900, 1000) == (False, 900), "E6d elapsed 为负(未来/回绕) -> 不限流")

# ------------------------------------------------------------------ E7 ACK 判据

sk7 = TcpSock(rcv_nxt=1000, snd_una=1000, snd_nxt=1000, max_snd_wnd=65536)
ok(ack_acceptable_rfc793(sk7, 1000) and ack_acceptable_rfc5961(sk7, 1000), "E7a SEG.ACK==SND.NXT 两者都接受")
ok(not ack_acceptable_rfc793(sk7, 1001) and not ack_acceptable_rfc5961(sk7, 1001),
   "E7b SEG.ACK > SND.NXT 两者都拒绝")
lo5961 = (1000 - 65536) & MASK32
ok(ack_acceptable_rfc5961(sk7, lo5961), "E7c RFC5961 下界 SND.UNA-MAX.SND.WND 恰被接受")
ok(not ack_acceptable_rfc5961(sk7, (lo5961 - 1) & MASK32), "E7d RFC5961 下界 -1 被拒")
ok(ack_acceptable_rfc793(sk7, (lo5961 - 1) & MASK32),
   "E7e 同一个 ACK 在 RFC793 判据下被接受 —— 收紧判据确实砍掉了这一段")
ok(ack_window_size(sk7, rfc5961=False) == 1 << 31, "E7f RFC793 可接受 ACK 取值数 = 2^31")
ok(ack_window_size(sk7, rfc5961=True) == 65537, "E7g RFC5961 可接受 ACK 取值数 = MAX.SND.WND+1")
ok(ack_window_size(sk7, False) / ack_window_size(sk7, True) > 32767,
   "E7h 收紧后问题空间缩小约 3 个数量级")
# 回绕：SND.UNA 接近 0 时两个判据的下界都绕到地址空间末尾
skw = TcpSock(rcv_nxt=0, snd_una=10, snd_nxt=10, max_snd_wnd=65536)
ok(ack_acceptable_rfc793(skw, MASK32) and ack_acceptable_rfc5961(skw, MASK32),
   "E7i ACK=0xFFFFFFFF 距 SND.UNA 仅 11，两个判据都接受（回绕处量的是模距离）")
ack_far = (skw.snd_una - 100000) & MASK32
ok(ack_acceptable_rfc793(skw, ack_far) and not ack_acceptable_rfc5961(skw, ack_far),
   "E7j 距 SND.UNA 十万的那个 ACK：RFC793 接受、RFC5961 拒绝（回绕下界被收紧）")
ok(ack_acceptable_rfc5961(skw, (skw.snd_una - 65536) & MASK32)
   and not ack_acceptable_rfc5961(skw, (skw.snd_una - 65537) & MASK32),
   "E7k RFC5961 的下界恰在 SND.UNA-MAX.SND.WND：等于则收、少 1 则拒")

# ------------------------------------------------------------------ E8 盲注扫描难度

SB, W = 12, 64
mismatch = 0
rng = random.Random(20260921)
starts = [rng.randrange(1 << SB) for _ in range(64)]
for st in starts:
    for exact in (False, True):
        a = sweep_tries(SB, W, exact, 1234, st)
        b = sweep_closed_form(SB, W, exact, 1234, st)
        if a != b:
            mismatch += 1
ok(mismatch == 0, "E8a 闭式与实际扫描完全一致（64 个起点 × 2 种判据，差异 %d）" % mismatch)

tot = sum(sweep_closed_form(SB, W, False, 1234, s) for s in range(1 << SB))
mean_no = sum(sweep_closed_form(SB, W, False, 1234, s) for s in range(1 << SB)) / float(1 << SB)
mean_ex = sum(sweep_closed_form(SB, W, True, 1234, s) for s in range(1 << SB)) / float(1 << SB)
ok(abs(mean_ex - 2048.5) < 1e-9, "E8b 精确判据下平均命中下标 = (N+1)/2 = 2048.5（实测 %.4f）" % mean_ex)
ok(32.0 <= mean_no <= 33.0, "E8c 窗口判据下平均命中下标 ≈ N/(2W) = 32（实测 %.4f）" % mean_no)
ok(abs(mean_no - mean_ex / W) <= 1.0, "E8d 两种判据的均值之比 ≈ 窗口大小（%.4f vs %.4f）" % (mean_no, mean_ex / W))
ok(sweep_closed_form(SB, W, True, 1234, 0) == 1235, "E8e 精确判据下 start=0 命中下标 = nxt+1")
ok(sweep_closed_form(SB, W, False, 1234, 1234) == 1, "E8f 起点恰好等于 RCV.NXT -> 第 1 个包就命中")
ok(sweep_closed_form(SB, W, False, 1234, 1233) == 2, "E8g 起点差 1（落在窗口左侧）-> 要第 2 个包")
ok(sweep_tries(SB, W, False, 1234, 1233) == 2, "E8g' 实扫验证同上")

ok(abs(mean_tries_continuous(32, 32768, False) - 65536.0) < 1e-6,
   "E8d RFC5961 §1.3：窗口 32768 -> 65536 包")
ok(int(mean_tries_continuous(32, 65535, False)) == 32768,
   "E8h 窗口 65535 -> 32768 包（实测 %.4f，文档取整）" % mean_tries_continuous(32, 65535, False))
ok(abs(mean_tries_continuous(32, 32768, True) - (1 << 31)) < 1e-6,
   "E8i 缓解后需 2^31 包（半个序列空间）")
ok(abs(mean_tries_continuous(32, 32768, False) * 2 - 131072.0) < 1e-6,
   "E8j 数据注入平均需 2^32/RCV.WND = 131072 次 —— 恰为 RST/SYN 的两倍")
ok(abs(mean_tries_continuous(32, 32768, True) / mean_tries_continuous(32, 32768, False) - 32768.0) < 1e-6,
   "E8k 缓解把难度放大了整整一个窗口大小")

# ------------------------------------------------------------------ E9 序列号比较工具

ok(seq_before(1, 2) and not seq_before(2, 1), "E9a before()")
ok(seq_before(MASK32, 0) and seq_before(MASK32, 2) and not seq_before(2, MASK32),
   "E9b 回绕处 0xFFFFFFFF 仍在 0 与 2 之前（bmod 2^31 窗口内）")
ok(to_signed32(1) == 1 and to_signed32(MASK32) == -1, "E9c to_signed32 回绕")
ok(seq_after(0, MASK32) and not seq_after(MASK32, 0), "E9d after() 与 before() 互补")
ok(tcp_sequence(fresh(), seg(1000)) is None, "E9e 窗口内的普通段无丢包原因")
ok(tcp_sequence(fresh(), seg(999)) == "OLD_SEQUENCE", "E9f end_seq < rcv_wup -> OLD_SEQUENCE")

print("PASS %d assertions" % PASS_COUNT)
sys.exit(0)
