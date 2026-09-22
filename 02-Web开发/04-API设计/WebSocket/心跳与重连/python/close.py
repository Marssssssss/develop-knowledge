"""RFC 6455 §5.5.1 关闭握手的状态机。

原文关键句（逐条对应下面的分支）：
  - "The application MUST NOT send any more data frames after sending a
     Close frame."                                  → may_send_data()
  - "If an endpoint receives a Close frame and did not previously send a
     Close frame, the endpoint MUST send a Close frame in response. (When
     sending a Close frame in response, the endpoint typically echos the
     status code it received.)"                     → on_recv_close()
  - "After both sending and receiving a Close message, an endpoint considers
     the WebSocket connection closed and MUST close the underlying TCP
     connection."                                   → state == CLOSED
  - "The server MUST close the underlying TCP connection immediately; the
     client SHOULD wait for the server to close the connection but MAY close
     the connection at any time after sending and receiving a Close message."
                                                    → tcp_action()
  - "If a client and server both send a Close message at the same time, both
     endpoints will have sent and received a Close message..."
                                                    → 同时关闭也是 CLOSED

状态用两个布尔量（是否发出 / 是否收到）合成，而不是单个枚举字段：
"已收未发" 与 "已发未收" 都是 CLOSING，但下一步动作完全不同，
用一个枚举字段会把这两者混为一谈，导致回 Close 被判成"重复发送"。
"""

OPEN = "open"
CLOSING = "closing"   # 只完成了一个方向
CLOSED = "closed"     # 收发都完成


class CloseHandshake:
    """跟踪本端在关闭握手中的状态。role 只影响 tcp_action 的建议。"""

    def __init__(self, role="client"):
        if role not in ("client", "server"):
            raise ValueError("role 必须是 client 或 server")
        self.role = role
        self.sent = False
        self.received = False
        self.sent_code = None
        self.recv_code = None
        self.violations = []

    @property
    def state(self):
        if self.sent and self.received:
            return CLOSED
        if self.sent or self.received:
            return CLOSING
        return OPEN

    # ---- 查询 ----
    def may_send_data(self):
        """发出 Close 之后就再也不能发数据帧。"""
        return not self.sent

    def may_send_ping(self):
        """Ping 也是帧；本 demo 按"Close 之后不再发任何帧"处理控制帧。

        原文只对 Ping 说 "unless it already received a Close frame"，
        对发送侧没明说；这里取工程上更严的一侧并在 README 标注口径。
        """
        return not self.sent

    def tcp_action(self):
        """关闭握手完成后，底层 TCP 该怎么处置。"""
        if self.state != CLOSED:
            return "keep"
        if self.role == "server":
            return "close-immediately"   # MUST close immediately
        return "wait-then-close"         # SHOULD wait，但 MAY 随时关

    # ---- 事件 ----
    def send_data(self):
        if not self.may_send_data():
            self.violations.append("已发出 Close 帧后仍然发送数据帧")
            return False
        return True

    def on_send_close(self, code=None):
        """本端发出 Close 帧。"""
        if self.sent:
            self.violations.append("重复发送 Close 帧")
            return False
        self.sent = True
        self.sent_code = code
        return True

    def on_recv_close(self, code=None):
        """收到对端 Close 帧。

        返回 (本端是否应当回一个 Close, 回显用的状态码)。
        "未发过就 MUST 回" 是原文要求；回显码也是原文 "typically echos"。
        """
        if self.received:
            self.violations.append("重复收到 Close 帧")
            return False, None
        self.received = True
        self.recv_code = code
        if not self.sent:
            return True, code
        return False, None


def simultaneous_close():
    """双方同时发 Close：两边都收发过，直接进 CLOSED。"""
    me = CloseHandshake("client")
    peer = CloseHandshake("server")
    me.on_send_close(1000)
    peer.on_send_close(1000)
    me.on_recv_close(1000)
    peer.on_recv_close(1000)
    return me.state, peer.state
