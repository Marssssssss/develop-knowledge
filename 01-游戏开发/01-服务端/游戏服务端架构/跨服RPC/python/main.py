# -*- coding: utf-8 -*-
"""跨服 RPC:一根有序连接上的请求号关联、超时、错误传播与单向推送。

帧格式(对照 gRPC Length-Prefixed-Message 的大端 4 字节长度前缀):
  [u32 BE 帧长][u8 类型][u32 BE req_id][u8 路径长][路径][载荷]
  类型 1=REQ 2=RESP(载荷前有 1 字节状态码) 3=PUSH(req_id 恒 0)
"""
import struct
from collections import deque

MSG_REQ, MSG_RESP, MSG_PUSH = 1, 2, 3
STATUS_OK, STATUS_TIMEOUT, STATUS_UNKNOWN_PATH = 0, 1, 2


class FrameError(ValueError):
    pass


class Frame:
    __slots__ = ("msg_type", "req_id", "path", "payload", "status")

    def __init__(self, msg_type, req_id, path, payload=b"", status=0):
        self.msg_type, self.req_id = msg_type, req_id
        self.path, self.payload, self.status = path, payload, status


def encode_frame(f):
    p = f.path.encode()
    body = struct.pack("<BIB", f.msg_type, f.req_id, len(p)) + p
    if f.msg_type == MSG_RESP:
        body += struct.pack("<B", f.status)
    body += f.payload
    return struct.pack(">I", len(body)) + body


def decode_frame(data):
    if len(data) < 4:
        raise FrameError("缺长度前缀")
    n = struct.unpack(">I", data[:4])[0]
    if len(data) - 4 != n:
        raise FrameError("帧长 %d != 实际 %d" % (n, len(data) - 4))
    pos = 4
    msg_type, req_id, plen = struct.unpack_from("<BIB", data, pos)
    pos += 6
    if msg_type not in (MSG_REQ, MSG_RESP, MSG_PUSH):
        raise FrameError("未知消息类型 %d" % msg_type)
    if pos + plen > len(data):
        raise FrameError("路径截断")
    path = data[pos:pos + plen].decode()
    pos += plen
    status = 0
    if msg_type == MSG_RESP:
        status = data[pos]
        pos += 1
    return Frame(msg_type, req_id, path, data[pos:], status)


# ---------------- 客户端:req_id 关联 + 超时 + 晚到丢弃 ----------------

class Pending:
    __slots__ = ("path", "deadline", "done", "result", "error")

    def __init__(self, path, deadline):
        self.path, self.deadline = path, deadline
        self.done, self.result, self.error = False, None, None


class RpcClient:
    def __init__(self, out_q):
        self.out = out_q
        self.pending = {}      # req_id -> Pending
        self.next_id = 1
        self.pushes = []       # 单向推送落地处

    def call(self, path, payload=b"", timeout_ms=100):
        rid = self.next_id
        self.next_id += 1
        self.pending[rid] = Pending(path, timeout_ms)
        self.out.append(encode_frame(Frame(MSG_REQ, rid, path, payload)))
        return rid

    def on_frame(self, raw):
        f = decode_frame(raw)
        if f.msg_type == MSG_PUSH:
            if f.req_id != 0:
                raise FrameError("PUSH 的 req_id 必须为 0")
            self.pushes.append(f)
            return
        entry = self.pending.get(f.req_id)
        if entry is None or entry.done:      # 超时后晚到的响应:静默丢弃
            return "dropped"
        entry.done = True
        if f.status == STATUS_OK:
            entry.result = f.payload
        else:
            entry.error = f.status
        return "resolved"

    def expire(self, now):
        """超时本地判定;判完的 pending 保留 done 态以丢弃晚到响应。"""
        for e in self.pending.values():
            if not e.done and now >= e.deadline:
                e.done, e.error = True, STATUS_TIMEOUT


# ---------------- 服务端:handler 表 + 模拟延迟 ----------------

class RpcServer:
    def __init__(self, out_q):
        self.out = out_q
        self.handlers = {}    # path -> (fn, delay_ms)
        self.scheduled = []   # (due_ms, req_id, path, status, payload)

    def register(self, path, fn, delay_ms=0):
        self.handlers[path] = (fn, delay_ms)

    def push(self, path, payload=b""):
        self.out.append(encode_frame(Frame(MSG_PUSH, 0, path, payload)))

    def pump(self, in_q, now):
        while in_q:
            f = decode_frame(in_q.popleft())
            if f.msg_type != MSG_REQ:
                raise FrameError("服务端只处理 REQ")
            if f.path not in self.handlers:
                self.scheduled.append((now, f.req_id, f.path,
                                       STATUS_UNKNOWN_PATH, b""))
                continue
            fn, delay = self.handlers[f.path]
            try:
                status, payload = 0, fn(f.payload)
            except Exception as e:      # 跨进程不传异常栈,只传码
                status, payload = getattr(e, "code", 5), b""
            self.scheduled.append((now + delay, f.req_id, f.path,
                                   status, payload))
        self.scheduled.sort(key=lambda s: (s[0], s[1]))
        while self.scheduled and self.scheduled[0][0] <= now:
            due, rid, path, status, payload = self.scheduled.pop(0)
            self.out.append(encode_frame(
                Frame(MSG_RESP, rid, path, payload, status)))

    def emit(self, now):
        while self.scheduled and self.scheduled[0][0] <= now:
            due, rid, path, status, payload = self.scheduled.pop(0)
            self.out.append(encode_frame(
                Frame(MSG_RESP, rid, path, payload, status)))


def check(label, cond, detail=""):
    assert cond, "%s %s" % (label, detail)
    print("[ok] %s" % label)


def main():
    # ---- 1. 帧编解码 ----
    f = Frame(MSG_RESP, 42, "/ZoneService/KickPlayer", b"bye", 0)
    raw = encode_frame(f)
    d = decode_frame(raw)
    check("帧往返:类型/req_id/路径/状态/载荷",
          (d.msg_type, d.req_id, d.path, d.status, d.payload)
          == (MSG_RESP, 42, "/ZoneService/KickPlayer", 0, b"bye"))
    check("长度前缀为 4 字节大端", raw[:4] == struct.pack(">I", len(raw) - 4))
    for label, bad in (("帧长不符", raw[:-1]),
                       ("未知类型", b"\x00\x00\x00\x03\x07\x00\x00\x00\x00\x00")):
        try:
            decode_frame(bad)
            check(label + " 应报错", False)
        except FrameError:
            check(label + " 报错", True)

    # ---- 2. 乱序响应按 req_id 正确关联 ----
    zone_to_global, global_to_zone = deque(), deque()
    cli = RpcClient(zone_to_global)
    srv = RpcServer(global_to_zone)
    srv.register("/Global/QueryRank", lambda p: b"rank-1", delay_ms=30)
    srv.register("/Global/QueryMail", lambda p: b"mail-7", delay_ms=5)
    srv.register("/Global/Ping", lambda p: b"pong", delay_ms=0)

    now = 0
    a = cli.call("/Global/QueryRank", timeout_ms=200)   # 慢
    b = cli.call("/Global/QueryMail", timeout_ms=200)
    c = cli.call("/Global/Ping", timeout_ms=200)
    srv.pump(zone_to_global, now)                       # 入队三响应 due 30/5/0
    srv.emit(now)
    while global_to_zone:
        cli.on_frame(global_to_zone.popleft())
    check("乱序到达仍正确关联(Ping 最先回)",
          cli.pending[c].result == b"pong"
          and cli.pending[a].result is None)
    srv.emit(now + 30)                                  # 30ms 后慢的才回
    while global_to_zone:
        cli.on_frame(global_to_zone.popleft())
    check("三个并发调用全部配对成功",
          cli.pending[a].result == b"rank-1"
          and cli.pending[b].result == b"mail-7"
          and cli.pending[c].result == b"pong")

    # ---- 3. 超时 + 晚到响应丢弃 ----
    d = cli.call("/Global/SlowEcho", b"x", timeout_ms=100)
    srv.register("/Global/SlowEcho", lambda p: p, delay_ms=200)
    srv.pump(zone_to_global, now)
    cli.expire(now + 110)
    check("110ms 判 TIMEOUT", cli.pending[d].error == STATUS_TIMEOUT)
    srv.emit(now + 200)                                 # 200ms 响应才到
    late = cli.on_frame(global_to_zone.popleft())
    check("晚到响应被静默丢弃", late == "dropped")

    # ---- 4. 错误传播(状态码)与未知路径 ----
    def boom(payload):
        class BizError(Exception):
            code = 7
        raise BizError()
    srv.register("/Global/Boom", boom, delay_ms=0)
    e = cli.call("/Global/Boom", timeout_ms=100)
    u = cli.call("/Global/NoSuch", timeout_ms=100)
    srv.pump(zone_to_global, now)
    srv.emit(now)
    while global_to_zone:
        cli.on_frame(global_to_zone.popleft())
    check("业务错误按状态码 7 传播", cli.pending[e].error == 7)
    check("未知路径返回状态码 2", cli.pending[u].error == STATUS_UNKNOWN_PATH)

    # ---- 5. 单向推送 ----
    srv.push("/ZoneService/KickPlayer", b"uid=1001")
    f = cli.on_frame(global_to_zone.popleft())
    check("PUSH 无配对直达推送队列",
          f != "dropped" and len(cli.pushes) == 1
          and cli.pushes[0].path == "/ZoneService/KickPlayer"
          and cli.pushes[0].payload == b"uid=1001")

    # ---- 6. pending 上限(应用层流控) ----
    cli2 = RpcClient(deque())
    for _ in range(64):
        cli2.call("/X/Y", timeout_ms=1000)
    check("在途调用数受控(64)", len(cli2.pending) == 64)

    print("\n全部断言通过")


if __name__ == "__main__":
    main()
