"""TCP 盲注入攻击与 RFC 5961 缓解模型。

逐行转写 Linux 内核 net/ipv4/tcp_input.c 的
  tcp_validate_incoming() / tcp_sequence() / tcp_reset_check()
  tcp_send_challenge_ack() / tcp_challenge_ack_allowed()
  __tcp_oow_rate_limited()
并与 RFC 5961 的规范文本对拍。

语言差异显式落地：
  - 内核 u32 序列号回绕 -> 这里一律先 & MASK32 再用 seq_before/seq_after 比较；
  - 内核 (s32)(a-b) 的有符号判定 -> to_signed32()；
  - 内核的 jiffies/HZ -> here now_sec / now_j 由调用方注入，便于确定化测试；
  - get_random_u32_inclusive() -> rand_u32 可注入。
"""

MASK32 = 0xFFFFFFFF
INT_MAX = 0x7FFFFFFF

# TCP 状态位（include/net/tcp_states.h：位号即状态值）
TCP_ESTABLISHED, TCP_SYN_RECV, TCP_CLOSE_WAIT = 1, 3, 5
TCP_LAST_ACK, TCP_CLOSING = 9, 11
RESET_CHECK_STATES = (1 << TCP_CLOSE_WAIT) | (1 << TCP_LAST_ACK) | (1 << TCP_CLOSING)


def to_signed32(v):
    v &= MASK32
    return v - (1 << 32) if v >> 31 else v


def seq_before(a, b):
    """内核 before(seq1, seq2)：(s32)(seq1-seq2) < 0"""
    return to_signed32((a - b) & MASK32) < 0


def seq_after(a, b):
    return seq_before(b, a)


# ---------------------------------------------------------------- 限速器

class Netns:
    """net->ipv4 中与 challenge ACK 相关的字段。"""

    def __init__(self, challenge_ack_limit=1000, invalid_ratelimit=500):
        self.sysctl_tcp_challenge_ack_limit = challenge_ack_limit
        self.sysctl_tcp_invalid_ratelimit = invalid_ratelimit
        self.tcp_challenge_timestamp = None
        self.tcp_challenge_count = 0


def challenge_ack_allowed(net, now_sec, rand_u32):
    """tcp_challenge_ack_allowed(): 每秒把计数复位成一个**随机值**。"""
    ack_limit = net.sysctl_tcp_challenge_ack_limit
    if ack_limit == INT_MAX:
        return True
    if now_sec != net.tcp_challenge_timestamp:
        half = (ack_limit + 1) >> 1
        net.tcp_challenge_timestamp = now_sec
        net.tcp_challenge_count = rand_u32(half, ack_limit + half - 1)
    if net.tcp_challenge_count > 0:
        net.tcp_challenge_count -= 1
        return True
    return False


def oow_rate_limited(net, now_j, last_oow_ack_time):
    """__tcp_oow_rate_limited(): 返回 (是否限流, 新的 last_oow_ack_time)。"""
    if last_oow_ack_time:
        elapsed = to_signed32((now_j - last_oow_ack_time) & MASK32)
        if 0 <= elapsed < net.sysctl_tcp_invalid_ratelimit:
            return True, last_oow_ack_time
    return False, now_j


# ---------------------------------------------------------------- 连接

class TcpSock:
    def __init__(self, rcv_nxt, snd_una=None, snd_nxt=None, rcv_wnd=32768,
                 state=TCP_ESTABLISHED, rcv_wup=None, max_snd_wnd=65536):
        self.rcv_nxt = rcv_nxt & MASK32
        self.rcv_wup = (rcv_nxt if rcv_wup is None else rcv_wup) & MASK32
        self.rcv_wnd = rcv_wnd
        self.snd_una = (rcv_nxt if snd_una is None else snd_una) & MASK32
        self.snd_nxt = (rcv_nxt if snd_nxt is None else snd_nxt) & MASK32
        self.max_snd_wnd = max_snd_wnd
        self.state = state
        self.sacks = []            # [(start, end), ...]
        self.syn_fastopen = False
        self.data_segs_in = True
        self.last_oow_ack_time = 0
        self.rcv_queue_len = 0

    def max_receive_window(self):
        """tcp_max_receive_window(): 内核用的是它而不是 rcv_wnd。"""
        return self.rcv_wnd


def reset_check(sk, seg):
    """tcp_reset_check(): seq == rcv_nxt-1 且状态属于三个关闭态。"""
    return (seg.get("seq", 0) & MASK32) == ((sk.rcv_nxt - 1) & MASK32) \
        and ((1 << sk.state) & RESET_CHECK_STATES) != 0


def tcp_sequence(sk, seg):
    """tcp_sequence(): 返回 None 表示可接受，否则返回丢包原因。"""
    end_seq = seg.get("end_seq", seg.get("seq", 0)) & MASK32
    fin = 1 if seg.get("fin") else 0
    if seq_before(end_seq, sk.rcv_wup):
        return "OLD_SEQUENCE"
    seq_limit = (sk.rcv_nxt + sk.max_receive_window()) & MASK32
    if seq_after(end_seq, seq_limit):
        if not seq_after((end_seq - fin) & MASK32, seq_limit):
            return None
        if seq_after(seg.get("seq", 0) & MASK32, seq_limit):
            return "INVALID_SEQUENCE"
        if sk.rcv_queue_len:
            return "INVALID_END_SEQUENCE"
    return None


# ---------------------------------------------------------------- 主判定

PASS = "pass"
RESET = "reset"
DISCARD = "discard"
CHALLENGE = "challenge_ack"


def validate_incoming(sk, seg, net, now_sec, now_j, rand_u32):
    """返回 (action, detail)。detail 说明走了哪条分支。"""
    seq = seg.get("seq", 0) & MASK32
    rst, syn, ack = bool(seg.get("rst")), bool(seg.get("syn")), bool(seg.get("ack"))

    def challenge(kind):
        limited, t = oow_rate_limited(net, now_j, sk.last_oow_ack_time)
        sk.last_oow_ack_time = t
        if limited:
            return (DISCARD, kind + "/oow_rate_limited")
        if challenge_ack_allowed(net, now_sec, rand_u32):
            return (CHALLENGE, kind)
        return (DISCARD, kind + "/netns_rate_limited")

    reason = tcp_sequence(sk, seg)
    if reason is not None:
        if not rst:
            if syn:
                return challenge("syn_challenge")
            return (DISCARD, "dupack/" + reason)
        if reset_check(sk, seg):
            return (RESET, "reset_check_out_of_window")
        return (DISCARD, "silent/" + reason)

    if rst:
        if seq == sk.rcv_nxt or reset_check(sk, seg):
            return (RESET, "seq_matches_rcv_nxt")
        if sk.sacks:
            max_sack = max(e for _, e in sk.sacks)
            if seq == (max_sack & MASK32):
                return (RESET, "seq_matches_max_sack_edge")
        return challenge("rst_challenge")

    if syn:
        if (sk.state == TCP_SYN_RECV and ack
                and (seq + 1) & MASK32 == seg.get("end_seq", 0) & MASK32
                and (seq + 1) & MASK32 == sk.rcv_nxt
                and seg.get("ack_seq", -1) & MASK32 == sk.snd_nxt):
            return (PASS, "syn_recv_retransmitted_ack")
        return challenge("syn_challenge")

    return (PASS, "ok")


# ---------------------------------------------------------------- ACK 校验

def ack_acceptable_rfc793(sk, ack_seq):
    """RFC 793 / RFC 5961 §5.1 描述的旧判据。"""
    lo = (sk.snd_una - (1 << 31) + 1) & MASK32
    return not seq_before(ack_seq, lo) and not seq_after(ack_seq, sk.snd_nxt)


def ack_acceptable_rfc5961(sk, ack_seq):
    """RFC 5961 §5.2 的收紧判据：(SND.UNA - MAX.SND.WND) <= SEG.ACK <= SND.NXT"""
    lo = (sk.snd_una - sk.max_snd_wnd) & MASK32
    return not seq_before(ack_seq, lo) and not seq_after(ack_seq, sk.snd_nxt)


def ack_window_size(sk, rfc5961=True):
    """可被判为可接受的 ACK 取值个数（含端点）。"""
    span = sk.max_snd_wnd if rfc5961 else (1 << 31) - 1
    return span + 1 + to_signed32((sk.snd_nxt - sk.snd_una) & MASK32)


# ---------------------------------------------------------------- 盲注扫描

def sweep_tries(space_bits, wnd, exact, nxt, start):
    """攻击者扫一遍序列空间所需的包数。

    exact=False：以窗口为步长扫（RFC 793 判据下窗口内即接受）；
    exact=True ：以 1 为步长扫（RFC 5961 判据下必须精确等于 RCV.NXT）。
    """
    n = 1 << space_bits
    step = 1 if exact else wnd
    nxt &= n - 1
    for i, g in enumerate(range(start & (n - 1), (start & (n - 1)) + n, step), start=1):
        g &= n - 1
        if exact:
            if g == nxt:
                return i
        elif (g - nxt) % n < wnd:
            return i
    return None


def sweep_closed_form(space_bits, wnd, exact, nxt, start):
    """闭式命中下标（1 起算）。

    exact=True ：步长 1，命中下标 = ((nxt-start) mod N) + 1。
    exact=False：步长 wnd。令 d = (nxt-start) mod N、d = k*wnd + r，
      第 k 个猜测恰好落在 nxt 之前 r 处（窗外），要等到第 k+1 个才落进窗口；
      只有 r == 0 时第 k 个就命中。因此下标 = k+1（r==0）或 k+2（r>0），
      再对 N/wnd 取模（扫完一轮即覆盖全空间）。
    """
    n = 1 << space_bits
    d = (nxt - start) % n
    if exact:
        return d + 1
    k, r = divmod(d, wnd)
    return ((k + (0 if r == 0 else 1)) % (n // wnd)) + 1


def mean_tries_continuous(space_bits, wnd, exact):
    """文档口径（连续近似）：无缓解 N/(2*wnd)，缓解后 N/2。"""
    n = 1 << space_bits
    return n / 2.0 if exact else n / (2.0 * wnd)
