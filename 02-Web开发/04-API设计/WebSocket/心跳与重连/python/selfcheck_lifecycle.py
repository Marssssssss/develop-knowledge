"""610 自检（二）：关闭握手 / 心跳账本 / 重连退避。

对应 close.py / heartbeat.py / reconnect.py。凡是涉及随机的行为都用
harness.Fixed 钉死，避免"通过"只是运气。
"""

import random

from harness import check, expect_errors, Fixed
from frames import OP_PONG, MAX_CONTROL_PAYLOAD
from close import CloseHandshake, simultaneous_close, OPEN, CLOSING, CLOSED
from heartbeat import Heartbeat, pong_for
from reconnect import (Backoff, should_reconnect, wait_before_retry,
                       INITIAL_BACKOFF, MULTIPLIER, MAX_BACKOFF, JITTER,
                       MIN_CONNECT_TIMEOUT, IMMEDIATE_RETRY)


def section_handshake():
    c = CloseHandshake("client")
    check(c.state == OPEN and c.may_send_data(), "初始可发数据")
    check(c.tcp_action() == "keep", "未关闭时保持 TCP")
    check(c.may_send_ping(), "初始可发 Ping")

    check(c.on_send_close(1000), "发出 Close")
    check(c.state == CLOSING and not c.may_send_data(), "发出后进入 CLOSING")
    check(not c.may_send_ping(), "发出 Close 后不再发 Ping")
    check(not c.send_data(), "发出 Close 后发数据被拒")
    expect_errors(c.violations, ("已发出 Close 帧后仍然发送数据帧",), (),
                  "违规被记录")
    check(not c.on_send_close(1000), "重复发 Close 被拒")
    expect_errors(c.violations, ("重复发送 Close 帧",), (), "重复发 Close 被记录")

    s = CloseHandshake("server")
    should, echo = s.on_recv_close(1000)
    check(should is True and echo == 1000, "未发过则 MUST 回并回显码")
    check(s.state == CLOSING, "收到后进入 CLOSING")
    again, code2 = s.on_recv_close(1000)
    check(not again and code2 is None, "重复收 Close 不再回")
    check(s.on_send_close(1000), "server 回 Close")
    check(s.state == CLOSED, "收发齐备 → CLOSED")
    check(s.tcp_action() == "close-immediately", "server MUST 立即关 TCP")

    c.on_recv_close(1000)
    check(c.state == CLOSED, "client 收发齐备 → CLOSED")
    check(c.tcp_action() == "wait-then-close", "client SHOULD 等 server 先关")

    check(simultaneous_close() == (CLOSED, CLOSED), "同时关闭 → 双方 CLOSED")

    try:
        CloseHandshake("proxy")
        check(False, "非法 role 应抛错")
    except ValueError:
        check(True, "非法 role 抛 ValueError")


def section_heartbeat():
    hb = Heartbeat(interval=30.0, pong_timeout=10.0)
    now = 500.0
    check(hb.ping_due(now), "初始应发 Ping")
    hb.send_ping(b"a", now)
    check(hb.outstanding() == 1, "记一笔待应答")
    check(not hb.ping_due(now + 100), "single_flight：未应答前不再发")

    loose = Heartbeat(interval=30.0, pong_timeout=10.0, single_flight=False)
    loose.send_ping(b"a", now)
    check(not loose.ping_due(now + 10), "未到 interval 不发")
    check(loose.ping_due(now + 31), "到 interval 则发")

    check(hb.recv_pong(b"a", now + 0.5) == "reply", "匹配 Pong → reply")
    check(abs(hb.last_rtt - 0.5) < 1e-9, "RTT = 0.5")
    check(hb.outstanding() == 0, "应答后清空")
    check(hb.ping_due(now + 100), "应答后可再发")

    check(hb.recv_pong(b"zz", now + 1) == "unsolicited", "非匹配 Pong → 单向心跳")
    check(hb.unsolicited_pongs == 1, "单向心跳计数")
    check(hb.last_rtt == 0.5, "单向心跳不应刷新 RTT")

    op, payload = hb.recv_ping(b"ping-1", now + 2)
    check(op == OP_PONG and payload == b"ping-1", "回 Pong 且载荷原样回显")
    check(pong_for(b"ping-1") == b"ping-1", "pong_for 原样返回")
    check(hb.recv_ping(b"x", now + 3, have_recv_close=True) is None,
          "已收过 Close 则不必回 Pong")

    # 连发两个 Ping，对端只答最近一个：更早的那个作废而非永久挂账
    hb2 = Heartbeat(interval=1.0, pong_timeout=10.0, single_flight=False)
    hb2.send_ping(b"p1", now)
    hb2.send_ping(b"p2", now + 1)
    check(hb2.outstanding() == 2, "两笔待应答")
    check(hb2.recv_pong(b"p2", now + 1.2) == "reply", "最近一个被应答")
    check(hb2.missed_pongs == 1 and hb2.outstanding() == 0, "更早的作废")
    check(abs(hb2.last_rtt - 0.2) < 1e-9, "RTT 按被应答那个算")

    # 活性：以"最后一次收到任意帧"计时，含单向 Pong
    hb3 = Heartbeat(interval=30.0, pong_timeout=10.0)
    hb3.recv_frame(now)
    check(hb3.is_alive(now + 10), "恰好等于超时阈值仍算活（闭区间）")
    check(not hb3.is_alive(now + 10.001), "超过阈值即判死")
    check(hb3.deadline(now) == now + 10.0, "deadline = 最后收帧 + 超时")
    hb3.recv_pong(b"unsolicited", now + 9.5)
    check(hb3.is_alive(now + 15), "单向 Pong 同样刷新活性")

    try:
        hb.send_ping(b"x" * (MAX_CONTROL_PAYLOAD + 1), now)
        check(False, "超长 Ping 应抛错")
    except ValueError:
        check(True, "Ping 载荷超过 125 抛 ValueError")


def section_reconnect():
    check((INITIAL_BACKOFF, MULTIPLIER, MAX_BACKOFF, JITTER, MIN_CONNECT_TIMEOUT)
          == (1.0, 1.6, 120.0, 0.2, 20.0), "gRPC 官方五个参数")

    b = Backoff()
    check(b.current == INITIAL_BACKOFF, "游标从 INITIAL_BACKOFF 起")
    w = b.next_wait(Fixed("mid"))
    check(abs(w - 1.0) < 1e-9, "中点 jitter 下首次等待 = 1.0")
    check(abs(b.current - 1.6) < 1e-9, "游标推进 1.0*1.6")
    w2 = b.next_wait(Fixed("mid"))
    check(abs(w2 - 1.6) < 1e-9, "第二次等待 = 1.6")
    check(abs(b.current - 1.6 * 1.6) < 1e-9, "游标再乘 1.6")

    # jitter 上下界：同一游标下等待必然落在 [0.8c, 1.2c]
    lo = Backoff().next_wait(Fixed("lo"))
    hi = Backoff().next_wait(Fixed("hi"))
    check(abs(lo - 0.8) < 1e-9 and abs(hi - 1.2) < 1e-9, "jitter 区间 ±20%")
    check(lo < 1.0 < hi, "jitter 确实发散到两侧")

    capped = Backoff()
    for _ in range(40):
        capped.next_wait(Fixed("mid"))
    check(abs(capped.current - MAX_BACKOFF) < 1e-9, "游标被 MAX_BACKOFF 封顶")
    check(abs(capped.next_wait(Fixed("mid")) - MAX_BACKOFF) < 1e-9,
          "封顶后等待恒为 120s")

    # 原文：替代实现不得比该算法更频繁地尝试 —— 下界不得为负
    check(Backoff(jitter=1.0).next_wait(Fixed("lo")) == 0.0, "jitter=1 时下界截到 0")

    b.reset()
    check(b.current == INITIAL_BACKOFF, "reset 回到 INITIAL_BACKOFF")

    now = 1000.0
    check(Backoff().connect_deadline(now) >= now + MIN_CONNECT_TIMEOUT,
          "连接尝试超时不短于 MIN_CONNECT_TIMEOUT")
    long_wait = Backoff()
    for _ in range(30):
        long_wait.next_wait(Fixed("mid"))
    check(long_wait.connect_deadline(now) >= now + MIN_CONNECT_TIMEOUT,
          "退避很长时仍不短于 MIN_CONNECT_TIMEOUT")

    for code, want in ((1000, False), (1001, True), (1002, False), (1003, False),
                       (1007, False), (1008, False), (1009, False), (1010, False),
                       (1011, True), (1012, True), (1013, True), (1014, True),
                       (1015, False)):
        check(should_reconnect(code) is want, "关闭码 %d 重连=%s" % (code, want))
    check(should_reconnect(4999) is True, "未知码保守重连")
    check(IMMEDIATE_RETRY == frozenset({1012, 1013}), "1012/1013 立即重试")

    rnd = random.Random(11)
    check(wait_before_retry(1000, Backoff(), rnd) is None, "1000 不重连")
    check(wait_before_retry(1013, Backoff(), rnd) == 0.0, "1013 立即重试")
    check(wait_before_retry(1001, Backoff(), rnd) > 0, "1001 走退避")
    bb = Backoff()
    first = wait_before_retry(1011, bb, rnd)
    second = wait_before_retry(1011, bb, rnd)
    check(second > first * 0.9, "连续重连等待递增（含 jitter）")


def run():
    section_handshake()
    section_heartbeat()
    section_reconnect()
