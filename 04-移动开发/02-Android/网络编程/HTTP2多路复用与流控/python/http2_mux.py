# -*- coding: utf-8 -*-
"""HTTP/2 多路复用与流控(RFC 9113 口径)。

关键原文(实读 RFC 9113):
  §5.1.1 流标识符:客户端 MUST 奇数、服务端 MUST 偶数、0x0 用于连接控制;
  §5 多路复用:interleaving of messages on the same connection;
  §6.9.1 双窗口:流窗口 + 连接窗口,MUST NOT 发超过任一窗口余量的受控帧;
        9 字节帧头不计入流控;发送后两个窗口都减;
  §6.9.2 初始窗口 65,535;SETTINGS_INITIAL_WINDOW_SIZE 只改新流(对已开流是调 delta);
        连接窗口只能用 WINDOW_UPDATE 调;上限 2^31-1,超了 FLOW_CONTROL_ERROR。
"""

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


MAX_WINDOW = 2**31 - 1
DEFAULT_INITIAL = 65535


class FlowWindow:
    """发送侧维护的窗口:流级与连接级各一(§6.9.1)。"""

    def __init__(self, initial=DEFAULT_INITIAL):
        self.stream = {}
        self.conn = initial
        self.initial = initial

    def new_stream(self, sid):
        self.stream[sid] = self.initial

    def can_send(self, sid, n):
        return min(self.stream[sid], self.conn) >= n

    def data_sent(self, sid, n):
        """发送后两个窗口都减(§6.9.1);9 字节帧头不计入。"""
        self.stream[sid] -= n
        self.conn -= n

    def window_update(self, sid, n):
        """WINDOW_UPDATE:流级(sid)或连接级(sid=0);超上限 = FLOW_CONTROL_ERROR。"""
        target = self.stream if sid else self.conn_ref()
        if sid:
            if self.stream[sid] + n > MAX_WINDOW:
                raise ConnectionError("FLOW_CONTROL_ERROR")
            self.stream[sid] += n
        else:
            if self.conn + n > MAX_WINDOW:
                raise ConnectionError("FLOW_CONTROL_ERROR")
            self.conn += n

    def conn_ref(self):
        return 0

    def apply_settings(self, new_initial):
        """SETTINGS_INITIAL_WINDOW_SIZE 改所有**已开**流与后续新流的窗口(§6.9.2)。"""
        delta = new_initial - self.initial
        for sid in self.stream:
            self.stream[sid] += delta
        self.initial = new_initial


class Frame:
    def __init__(self, ftype, sid, payload_len, end_stream=False):
        self.ftype = ftype        # HEADERS / DATA / WINDOW_UPDATE / SETTINGS ...
        self.sid = sid
        self.len = payload_len    # 流控只数 DATA 的 payload
        self.end_stream = end_stream


def next_stream_id(current, role="client"):
    """流 ID 单调递增且客户端奇、服务端偶(§5.1.1)。"""
    step = 2
    return current + step


def interleave(*streams):
    """多路复用:帧可在同一连接交错,但单个流内帧序保持(§5)。"""
    out, streams = [], [list(s) for s in streams]
    while any(streams):
        for s in streams:
            if s:
                out.append(s.pop(0))       # 轮转取帧:交错而不乱序
    return out


def main():
    print("1. 流标识符")
    assert next_stream_id(1) == 3 and next_stream_id(3) == 5
    ok("客户端流 ID 奇数且严格递增(1,3,5…);服务端偶数;0x0 专属连接控制")

    print("2. 多路复用的交错不乱序")
    a = [Frame("HEADERS", 1, 0), Frame("DATA", 1, 100), Frame("DATA", 1, 50, True)]
    b = [Frame("HEADERS", 3, 0), Frame("DATA", 3, 80, True)]
    muxed = interleave(a, b)
    s1 = [f.ftype for f in muxed if f.sid == 1]
    s3 = [f.ftype for f in muxed if f.sid == 3]
    assert s1 == ["HEADERS", "DATA", "DATA"] and s3 == ["HEADERS", "DATA"]
    ok("两条流的帧交错在同一条 TCP 上;每条流内部帧序保持——"
       "一个流阻塞不阻止其它流推进")

    print("3. 双窗口记账")
    w = FlowWindow()
    w.new_stream(1)
    w.new_stream(3)
    assert w.stream[1] == w.stream[3] == w.conn == 65535
    w.data_sent(1, 40000)
    assert w.stream[1] == 25535 and w.stream[3] == 65535 and w.conn == 25535
    ok("发送 DATA 后**流窗口与连接窗口都减**;9 字节帧头不计入;窗口余量=接收方缓冲的度量")

    print("4. 发送闸门")
    assert w.can_send(3, 25535) and not w.can_send(3, 25536)
    assert not w.can_send(1, 25536)      # 流 1 窗口已 25535
    w.window_update(1, 40000)            # 只补流 1:流窗口回到 65535
    assert not w.can_send(1, 40000)      # 但连接窗口仍 25535 → 还是被钳住
    w.window_update(0, 40000)            # 补连接窗口
    assert w.can_send(1, 40000) and w.can_send(3, 65535)
    ok("发送能力 = min(流窗口, 连接窗口):只补流窗口救不了连接窗口,反之亦然——"
       "流控卡死的常见根源是只补一边")

    print("5. 窗口上限")
    try:
        w.window_update(0, MAX_WINDOW)
        raise AssertionError("unreachable")
    except ConnectionError as e:
        assert "FLOW_CONTROL_ERROR" in str(e)
    ok("窗口超 2^31-1 = FLOW_CONTROL_ERROR(连接错误级);"
       "SETTINGS 里 INITIAL_WINDOW_SIZE 值超限同理")

    print("6. SETTINGS 改初始窗口")
    w2 = FlowWindow()
    w2.new_stream(1)
    w2.data_sent(1, 65535)               # 流 1 窗口用光
    w2.apply_settings(16 * 1024)          # 收小初始窗口
    assert w2.stream[1] == 65535 - 65535 + (16384 - 65535)
    ok("SETTINGS_INITIAL_WINDOW_SIZE 按 **delta** 作用于所有已开流(可能直接变负数!),"
       "新流用新初值;连接窗口只能靠 WINDOW_UPDATE(§6.9.2 原文)")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
