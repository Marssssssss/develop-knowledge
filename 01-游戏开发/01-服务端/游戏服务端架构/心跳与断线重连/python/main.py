# 应用层可靠性:ack 位图 / 心跳超时 / 断线重连(Gaffer On Games 方案)
#
# 依据 Glenn Fiedler《Reliability and Congestion Avoidance over UDP》:
#   包头 [proto_id][seq][ack][ack_bits];每个包恒定携带 33 个 ack
#   (ack=最高已收序号 + 32 位位图覆盖其前 32 个);永不重发同一序号,
#   丢包由应用层用新序号重发;1 秒内未 ack 视为丢失;RTT 用 10% EMA 平滑;
#   序号回绕用「差值小于半量程则比大小,否则反向」技巧。
# 心跳/超时依据其《Client Server Connection》:稳态流量下无需专门
# keep-alive,5 秒无包即判超时。重连 = 会话保留 + 断点续传 + 消息号去重。

MASK = 0xFFFF
HALF = 0x8000
ACK_WINDOW = 32
LOSS_TIMEOUT_MS = 1000
RTT_ALPHA = 0.1          # EMA 平滑系数(原文 "10% seems to work well")
SESSION_TIMEOUT_MS = 5000


def seq_more_recent(s1, s2):
    """16 位回绕安全比较(Gaffer:差值 < 半量程按大小比,否则反向)。"""
    d = (s1 - s2) & MASK
    return d != 0 and d < HALF


class Reliability:
    """单向可靠层状态机:发送方视角跟踪 ack,接收方维护 ack 位图。"""

    def __init__(self):
        self.sent_at = {}        # seq -> 发送时刻(算 RTT/丢包)
        self.acked = set()
        self.remote_seq = None   # 收到的最高序号
        self.recv_window = []    # 最近 33 个已收序号
        self.rtt_ms = None

    # -- 接收方 --
    def on_recv(self, seq):
        if self.remote_seq is None or seq_more_recent(seq, self.remote_seq):
            self.remote_seq = seq
        self.recv_window.append(seq)
        if len(self.recv_window) > ACK_WINDOW + 1:
            self.recv_window.pop(0)

    def make_ack(self):
        """恒定 33 个 ack:ack + 32 位位图(冗余对抗 ack 本身丢包)。"""
        if self.remote_seq is None:
            return None, 0
        bits = 0
        have = set(self.recv_window)
        for n in range(1, ACK_WINDOW + 1):
            if (self.remote_seq - n) & MASK in have:
                bits |= 1 << (n - 1)
        return self.remote_seq, bits

    # -- 发送方 --
    def on_send(self, seq, now_ms):
        self.sent_at[seq] = now_ms

    def process_ack(self, ack, bits, now_ms):
        """对端来包携带的 ack 位图 -> 标记送达 + RTT EMA。"""
        for n in range(0, ACK_WINDOW + 1):
            seq = (ack - n) & MASK
            hit = n == 0 or (bits >> (n - 1)) & 1
            if not hit or seq in self.acked:
                continue
            self.acked.add(seq)
            if seq in self.sent_at:
                sample = now_ms - self.sent_at[seq]
                self.rtt_ms = (sample if self.rtt_ms is None
                               else self.rtt_ms * (1 - RTT_ALPHA) + sample * RTT_ALPHA)

    def infer_lost(self, now_ms):
        """1 秒未 ack 即判丢(Gaffer:30pps 下 1 秒冗余 ack 必达)。"""
        return [s for s, t in self.sent_at.items()
                if s not in self.acked and now_ms - t > LOSS_TIMEOUT_MS]


class Channel:
    """确定性丢包信道:drop 数据包与 drop 回程 ack 包独立可配。"""

    def __init__(self, drop_data=None, drop_ack=None, latency_ms=37):
        self.drop_data = drop_data or set()
        self.drop_ack = drop_ack or set()
        self.latency_ms = latency_ms

    def deliver(self, seq):        # 数据包方向
        return seq not in self.drop_data

    def ack_passes(self, seq):     # 携带 ack 的回程包方向
        return seq not in self.drop_ack


class Session:
    """服务器会话:心跳保活 + 5 秒超时回收(Gaffer Client Server)。"""

    def __init__(self, token, now_ms):
        self.token = token
        self.last_seen = now_ms

    def heartbeat(self, now_ms):
        self.last_seen = now_ms

    def expired(self, now_ms):
        return now_ms - self.last_seen > SESSION_TIMEOUT_MS


class ServerSessions:
    def __init__(self, max_slots=64):
        self.max_slots = max_slots
        self.sessions = {}        # token -> Session

    def accept(self, token, now_ms):
        if token in self.sessions:
            return True
        if len(self.sessions) >= self.max_slots:
            return False
        self.sessions[token] = Session(token, now_ms)
        return True

    def sweep(self, now_ms):
        dead = [t for t, s in self.sessions.items() if s.expired(now_ms)]
        for t in dead:
            del self.sessions[t]
        return dead

    def kick_all(self):
        n = len(self.sessions)
        self.sessions.clear()
        return n


def check(label, cond, detail=""):
    assert cond, "%s %s" % (label, detail)
    print("[ok] %s" % label)


def main():
    # ---- 1. 序号回绕(Gaffer 技巧) ----
    check("回绕:2 比 65535 新", seq_more_recent(2, 65535))
    check("回绕:0 比 65535 新", seq_more_recent(0, 65535))
    check("不回绕:5 比 3 新", seq_more_recent(5, 3))
    check("回绕:65535 不比 2 新", not seq_more_recent(65535, 2))
    check("相等不算更新", not seq_more_recent(7, 7))

    # ---- 2. ack 位图编码/解码对称 ----
    r = Reliability()
    for s in (10, 11, 13, 42):
        r.on_recv(s)
    ack, bits = r.make_ack()
    check("ack 取最高已收序号 42", ack == 42)
    # 位图 bit n 对应 seq = ack - n;n=1 -> 41(未收),10 = 42-32 -> bit32
    check("位图:41 未收 bit0=0", (bits >> 0) & 1 == 0)
    check("位图:10 = 42-32 命中 bit32", (bits >> 31) & 1 == 1)
    check("位图:11 = 42-31 命中 bit31", (bits >> 30) & 1 == 1)
    check("位图:12 未收 bit30=0", (bits >> 29) & 1 == 0)

    # ---- 3. 双向交换 + 冗余 ack:ack 包也丢,ack 仍达 ----
    A, B = Reliability(), Reliability()
    ch = Channel(drop_data={s for s in range(100) if s % 7 == 3},
                 drop_ack={s for s in range(100) if s % 5 == 1})
    now = 0
    for i in range(100):                       # A 连发 100(序号不重发)
        if ch.deliver(i):
            B.on_recv(i)
        A.on_send(i, now)
        if ch.ack_passes(i):                   # B 每收一包回一个携带 ack 的包
            ack, bits = B.make_ack()
            A.process_ack(ack, bits, now + ch.latency_ms)
        now += 33
    delivered = {s for s in range(100) if s % 7 != 3}
    check("回程丢 20% ack 包后,送达集仍完整",
          delivered <= A.acked, "缺 %r" % (delivered - A.acked))
    check("丢的 15 个数据包不会出现在 acked", not (A.acked - delivered))
    check("RTT EMA 收敛到链路延迟量级",
          A.rtt_ms is not None and 30 <= A.rtt_ms <= 45, "got %s" % A.rtt_ms)

    # ---- 4. 丢包推断:1 秒未 ack ----
    lost = A.infer_lost(now + 1100)
    check("1.1s 后丢包推断 == 被丢的 15 个",
          set(lost) == {s for s in range(100) if s % 7 == 3})

    # ---- 5. 心跳超时与会话回收 ----
    ss = ServerSessions(max_slots=2)
    check("会话接入", ss.accept("tok-a", 0) and ss.accept("tok-b", 0))
    check("满员拒绝第三个", not ss.accept("tok-c", 0))
    ss.sessions["tok-a"].heartbeat(3000)
    check("3s 时都未超时", ss.sweep(4000) == [])
    check("5s 无包的 tok-b 被回收", ss.sweep(5100) == ["tok-b"])
    check("有心跳的 tok-a 存活", "tok-a" in ss.sessions)
    check("回收后槽位可复用", ss.accept("tok-c", 5200))

    # ---- 6. 断线重连:消息号去重 + 未送达重发(新序号) ----
    class Client:
        def __init__(self):
            self.outbox = {}          # msg_id -> data(未确认)
            self.processed = set()    # 服务端已处理(去重依据)
            self.next_seq = 0

        def send(self, msg_id, data):
            self.outbox[msg_id] = data

        def pump(self, now, drop):
            """把未确认消息用『新序号』发出(永不重发旧序号)。"""
            out = []
            for msg_id, data in list(self.outbox.items()):
                seq = self.next_seq
                self.next_seq = (self.next_seq + 1) & MASK
                if not drop:
                    out.append((seq, msg_id, data))
            return out

    class Srv:
        def __init__(self):
            self.delivered = []       # 应用层恰好一次的投递记录

        def on_packet(self, msg_id, data):
            if msg_id not in seen:
                seen.add(msg_id)
                self.delivered.append((msg_id, data))
                return True
            return False

    cl, seen, srv = Client(), set(), Srv()
    for m in range(20):
        cl.send(m, b"msg-%d" % m)
    # 第一段:序号 0..19 发出,其中 msg 5/11/17 的包被丢
    drops = {5, 11, 17}
    for seq, mid, data in cl.pump(0, False):
        if seq in drops:
            continue
        srv.on_packet(mid, data)
        if mid in cl.outbox:
            del cl.outbox[mid]        # 送达即出队(简化:应用层 ack)
    check("第一段后服务端收到 17 条", len(srv.delivered) == 17)
    # 断线 1.2s(> 1s 丢包推断阈值)后重连,未确认的 3 条重发(新序号)
    for seq, mid, data in cl.pump(1200, False):
        srv.on_packet(mid, data)
        cl.outbox.pop(mid, None)
    check("重连后 20 条全部投递", len(srv.delivered) == 20)
    check("消息号去重:无重复投递",
          len({m for m, _ in srv.delivered}) == 20)
    check("投递内容正确",
          sorted(m for m, _ in srv.delivered) == list(range(20))
          and all(d == b"msg-%d" % m for m, d in srv.delivered))
    check("客户端发件箱清空", not cl.outbox)

    print("\n全部断言通过")


if __name__ == "__main__":
    main()
