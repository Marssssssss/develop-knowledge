"""610 · WebSocket 心跳与重连 —— 演示入口。

分五段：控制帧硬约束 → Close 帧与状态码 → 关闭握手 → 心跳账本 → 重连退避。
"""

import random

from frames import (OP_CLOSE, OP_PING, OP_PONG, OP_TEXT, MAX_CONTROL_PAYLOAD,
                    validate_control_frame, encode_close_body, parse_close_body,
                    status_code_issues, code_range, is_registerable)
from close import CloseHandshake, simultaneous_close
from heartbeat import Heartbeat
from reconnect import Backoff, should_reconnect, wait_before_retry


def show(title):
    print("\n== %s ==" % title)


def main():
    show("控制帧的三条硬约束（§5.5 / §5.4）")
    for op, fin, length, note in [
        (OP_PING, True, 4, "合法 Ping"),
        (OP_PING, True, 126, "载荷 126 字节"),
        (OP_PING, False, 4, "Ping 被分片"),
        (OP_TEXT, True, 4, "数据帧冒充控制帧"),
        (0xB, True, 4, "保留控制帧 0xB"),
    ]:
        errs = validate_control_frame(op, fin, length)
        print("  0x%x fin=%-5s len=%-4d %-16s → %s"
              % (op, fin, length, note, errs or "合法"))
    print("  控制帧上限 %d 字节；控制帧可插在分片消息中间（§5.4）" % MAX_CONTROL_PAYLOAD)

    show("Close 帧编解码（§5.5.1：状态码网络字节序）")
    body, errs = encode_close_body(1000, "bye")
    print("  encode(1000,'bye') →", body.hex(" "), errs)
    print("  parse →", parse_close_body(body))
    print("  parse(空 body) →", parse_close_body(b""))
    print("  parse(1 字节) →", parse_close_body(b"\x03"))
    bad, errs = encode_close_body(1005)
    print("  encode(1005) →", bad, errs)

    show("状态码治理（§7.4.1 / §7.4.2 / IANA 注册表）")
    for code in (1000, 1005, 1006, 1015, 1011, 1013, 2000, 3000, 4500, 6000, 999):
        errs = status_code_issues(code)
        print("  %-5d %-38s %-32s %s"
              % (code, code_range(code),
                 "可注册" if is_registerable(code) else "不可注册",
                 errs[0] if errs else "可作为 Close 码发出"))

    show("关闭握手（§5.5.1）")
    cli = CloseHandshake("client")
    srv = CloseHandshake("server")
    print("  初始 client 可发数据:", cli.may_send_data())
    print("  client → Close(1000)，发出前 may_send_data =", cli.may_send_data())
    cli.on_send_close(1000)
    print("  client 已发 Close，may_send_data =", cli.may_send_data())
    cli.send_data()
    print("  违规记录:", cli.violations)
    should, echo = srv.on_recv_close(1000)
    print("  server 收到 Close → 应回 Close:", should, "回显码:", echo)
    srv.on_send_close(echo)
    cli.on_recv_close(1000)
    print("  client state =", cli.state, "→ tcp:", cli.tcp_action())
    print("  server state =", srv.state, "→ tcp:", srv.tcp_action())
    print("  双方同时发 Close →", simultaneous_close())

    show("心跳账本（§5.5.2 / §5.5.3）")
    hb = Heartbeat(interval=30.0, pong_timeout=10.0)
    now = 1000.0
    print("  首次 ping_due:", hb.ping_due(now))
    hb.send_ping(b"seq-1", now)
    print("  发出 Ping(b'seq-1')，single_flight 下 ping_due:", hb.ping_due(now + 60))
    print("  对端回 Pong(b'seq-1') →", hb.recv_pong(b"seq-1", now + 0.2),
          "RTT =", round(hb.last_rtt, 3))
    print("  收到非匹配 Pong(b'zzz') →", hb.recv_pong(b"zzz", now + 0.3),
          "（单向心跳，不期待应答）")
    hb.send_ping(b"seq-2", now + 30)
    hb.send_ping(b"seq-3", now + 31)
    print("  连发两个 Ping 后 outstanding =", hb.outstanding())
    print("  对端只答最近一个 →", hb.recv_pong(b"seq-3", now + 31.5))
    print("  更早的未被应答数 missed =", hb.missed_pongs, "剩余 outstanding =",
          hb.outstanding())
    print("  活性判定：now+40 →", hb.is_alive(now + 40), "；now+60 →",
          hb.is_alive(now + 60))
    print("  收到 Ping 时已收过 Close → 回 Pong?", hb.recv_ping(b"x", now,
                                                          have_recv_close=True))

    show("重连退避（gRPC connection-backoff.md 参数）")
    rnd = random.Random(7)
    b = Backoff()
    seq = [round(x, 2) for x in b.schedule(9, rnd)]
    print("  等待序列(带 ±20% jitter):", seq)
    b.reset()
    print("  reset 后重新从 INITIAL_BACKOFF 开始:", round(b.next_wait(rnd), 2))
    for code in (1000, 1001, 1011, 1013, 1008, 1015):
        b2 = Backoff()
        print("  关闭码 %-5d 重连? %-5s 等待 %s 秒"
              % (code, should_reconnect(code), wait_before_retry(code, b2, rnd)))

    print("\n（完整断言见 python/selfcheck_ws.py）")


if __name__ == "__main__":
    main()
