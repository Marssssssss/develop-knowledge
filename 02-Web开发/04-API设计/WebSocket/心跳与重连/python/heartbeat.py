"""RFC 6455 §5.5.2 / §5.5.3 心跳语义。

原文约束（逐条对应）：
  - "Upon receipt of a Ping frame, an endpoint MUST send a Pong frame in
     response, unless it already received a Close frame."  → on_recv_ping()
  - "A Pong frame sent in response to a Ping frame must have identical
     'Application data' as found in the message body of the Ping frame
     being replied to."                                    → pong_for()
  - "If an endpoint receives a Ping frame and has not yet sent Pong frame(s)
     in response to previous Ping frame(s), the endpoint MAY elect to send a
     Pong frame for only the most recently processed Ping frame."
                                                           → 允许丢旧应答
  - "A Pong frame MAY be sent unsolicited. This serves as a unidirectional
     heartbeat. A response to an unsolicited Pong frame is not expected."
                                                           → recv_pong 未匹配
  - "A Ping frame may serve either as a keepalive or as a means to verify
     that the remote endpoint is still responsive."

工程口径（RFC 未规定，本 demo 显式声明）：
  - 超时判定用"最后一次收到任意帧"计时，而不是只算 Pong —— 单向 Pong
    （unidirectional heartbeat）同样证明对端活着。
  - single_flight：未收到上一个 Pong 之前不再发新 Ping，避免排队堆积。
"""

from frames import MAX_CONTROL_PAYLOAD, OP_PING, OP_PONG


class Heartbeat:
    """Ping/Pong 的收发账本。时间为单调秒（float），由调用方注入。"""

    def __init__(self, interval=30.0, pong_timeout=10.0, single_flight=True):
        self.interval = interval
        self.pong_timeout = pong_timeout
        self.single_flight = single_flight
        self.pending = {}          # payload bytes -> 发出时刻
        self.last_ping_at = None   # 最后一次发出 Ping 的时刻
        self.last_recv_at = None   # 最后一次收到**任意**帧的时刻
        self.last_rtt = None
        self.unsolicited_pongs = 0
        self.missed_pongs = 0

    # ---- 发送侧 ----
    def ping_due(self, now):
        """是否该发下一个 Ping。

        两个条件都满足才发：距上次发 Ping 已超过 interval；且（若开启
        single_flight）当前没有未被应答的 Ping。
        """
        if self.single_flight and self.pending:
            return False
        if self.last_ping_at is None:
            return True
        return now - self.last_ping_at >= self.interval

    def send_ping(self, payload, now):
        """记一笔待应答的 Ping。载荷必须 ≤ 125 字节（控制帧上限）。

        载荷应当每次不同（例如序号 + 时间戳），否则会被当作重复键覆盖，
        无法区分是对哪一次 Ping 的应答。
        """
        if len(payload) > MAX_CONTROL_PAYLOAD:
            raise ValueError("Ping 载荷 %d 字节超过 125 上限" % len(payload))
        self.pending[payload] = now
        self.last_ping_at = now
        return (OP_PING, payload)

    # ---- 接收侧 ----
    def recv_ping(self, payload, now, have_recv_close=False):
        """收到 Ping：除非此前已收到 Close，否则 MUST 回 Pong。"""
        self.last_recv_at = now
        if have_recv_close:
            return None
        return (OP_PONG, payload)   # 载荷必须原样回显

    def recv_pong(self, payload, now):
        """收到 Pong：能匹配上就算 RTT，匹配不上就是单向心跳。

        RFC 允许对端只应答最近一个 Ping，所以这里收到匹配项时会把**更早**
        的待应答项一并作废 —— 不能假设"发了 N 个 Ping 就必有 N 个 Pong"。
        """
        self.last_recv_at = now
        if payload in self.pending:
            sent_at = self.pending.pop(payload)
            self.last_rtt = now - sent_at
            for key in [k for k, v in self.pending.items() if v <= sent_at]:
                del self.pending[key]
                self.missed_pongs += 1
            return "reply"
        self.unsolicited_pongs += 1
        return "unsolicited"

    def recv_frame(self, now):
        """收到任意数据帧也要刷新活性计时。"""
        self.last_recv_at = now

    # ---- 判定 ----
    def is_alive(self, now):
        """距最后一次收到帧未超过 pong_timeout 即认为连接仍活。"""
        if self.last_recv_at is None:
            return True
        return now - self.last_recv_at <= self.pong_timeout

    def deadline(self, now):
        return (self.last_recv_at if self.last_recv_at is not None else now) + self.pong_timeout

    def outstanding(self):
        return len(self.pending)


def pong_for(ping_payload):
    """§5.5.3：应答 Pong 的载荷必须与所应答 Ping 完全一致。"""
    return ping_payload
