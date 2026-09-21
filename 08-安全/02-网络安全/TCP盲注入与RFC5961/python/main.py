"""TCP 盲注入与 RFC 5961：把内核判定与攻击难度打印成证据表。

运行：python main.py
"""

from tcp5961 import (
    MASK32, Netns, TcpSock, validate_incoming, ack_acceptable_rfc793,
    ack_acceptable_rfc5961, ack_window_size, mean_tries_continuous,
    challenge_ack_allowed, sweep_closed_form, TCP_CLOSE_WAIT,
)


def fixed_rand(v):
    return lambda lo, hi: min(max(v, lo), hi)


def seg(seq, **kw):
    payload = kw.pop("payload", 0)
    fin = 1 if kw.get("fin") else 0
    syn = 1 if kw.get("syn") else 0
    kw["seq"] = seq
    kw["end_seq"] = (seq + payload + fin + syn) & MASK32
    kw.setdefault("rst", False)
    kw.setdefault("syn", False)
    kw.setdefault("ack", False)
    kw.setdefault("fin", False)
    kw.setdefault("ack_seq", 0)
    return kw


def probe(seq, **kw):
    sk = TcpSock(rcv_nxt=1000, rcv_wnd=32768, snd_una=1000, snd_nxt=1000,
                 max_snd_wnd=65536, state=kw.pop("state", 1))
    return validate_incoming(sk, seg(seq, **kw), Netns(), 1000, 100000, fixed_rand(1000))


print("== 1. RST 的三分支（RCV.NXT=1000, RCV.WND=32768）==")
for s in (999, 1000, 1001, 20000, 33768, 33769):
    print("   RST seq=%-7d -> %s" % (s, probe(s, rst=True)))
print("   同上但处于 CLOSE_WAIT: seq=999 -> %s" % (probe(999, rst=True, state=TCP_CLOSE_WAIT),))

print("\n== 2. SYN 与 RST 的判据差异（RFC 5961 §3.2 vs §4.2）==")
for s in (999, 1000, 1001, 99999):
    print("   SYN seq=%-7d -> %s" % (s, probe(s, syn=True)))
print("   => SYN 无论序列号落在哪都只发 challenge ACK；RST 在窗外则静默丢弃")

print("\n== 3. ACK 可接受区间（SND.UNA=SND.NXT=1000, MAX.SND.WND=65536）==")
sk = TcpSock(rcv_nxt=1000, snd_una=1000, snd_nxt=1000, max_snd_wnd=65536)
print("   可接受取值个数  RFC793=%d   RFC5961=%d   比值=%.1f" % (
    ack_window_size(sk, False), ack_window_size(sk, True),
    ack_window_size(sk, False) / ack_window_size(sk, True)))
for a in (1000, 1001, 99000, (1000 - 65536) & MASK32, (1000 - 65537) & MASK32, (1000 - 70000) & MASK32):
    print("   ACK=%-12d RFC793=%-5s RFC5961=%s" % (
        a, ack_acceptable_rfc793(sk, a), ack_acceptable_rfc5961(sk, a)))

print("\n== 4. challenge ACK 限速（tcp_challenge_ack_allowed，limit=1000）==")
for rnd in (500, 1000, 1499):
    net = Netns(challenge_ack_limit=1000)
    n = 0
    while challenge_ack_allowed(net, 1, fixed_rand(rnd)):
        n += 1
    print("   随机初值=%-5d -> 该秒允许 %d 次（ack_limit=1000，half=500）" % (rnd, n))
print("   注意：上界是 ack_limit+half-1=1499，不是 ack_limit —— 单秒实际可发出的比 limit 多")

print("\n== 5. 盲注扫描平均所需包数（文档口径 N/(2*WND)）==")
for wnd in (32768, 65535):
    print("   窗口 %-6d 无缓解=%.1f  缓解后=%.0f  放大倍数=%.0f" % (
        wnd, mean_tries_continuous(32, wnd, False), mean_tries_continuous(32, wnd, True),
        mean_tries_continuous(32, wnd, True) / mean_tries_continuous(32, wnd, False)))
print("   数据注入 = 2^32/RCV.WND = %.0f 次（恰为 RST/SYN 的两倍，故 RFC 定为 MAY）" % (
    mean_tries_continuous(32, 32768, False) * 2,))

print("\n== 6. 小规模穷举核对（N=2^12, WND=64）==")
m_no = sum(sweep_closed_form(12, 64, False, 1234, s) for s in range(1 << 12)) / float(1 << 12)
m_ex = sum(sweep_closed_form(12, 64, True, 1234, s) for s in range(1 << 12)) / float(1 << 12)
print("   窗口判据平均下标=%.4f（≈N/(2W)=32）；精确判据=%.4f（=(N+1)/2）" % (m_no, m_ex))
