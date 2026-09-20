"""HTTP/2 帧层 / 流控 / HPACK —— 自检。

断言策略：全部拿 **RFC 附录里的官方向量** 对拍（字节级），
流控直接用 §6.9.2 官方例子里的 −44 KB；
动态表大小用附录 C.3.1 官方标注的 s=57 反查条目大小公式。
"""

from main import (ConnectionError, ENTRY_OVERHEAD, FRAME_TYPES, FlowControl,
                  HpackDecoder, INITIAL_VALUES, MAX_FRAME_SIZE_MAX,
                  MAX_FRAME_SIZE_MIN, SETTINGS_ENABLE_PUSH,
                  SETTINGS_HEADER_TABLE_SIZE, SETTINGS_INITIAL_WINDOW_SIZE,
                  SETTINGS_MAX_CONCURRENT_STREAMS, SETTINGS_MAX_FRAME_SIZE,
                  SETTINGS_MAX_HEADER_LIST_SIZE, STATIC_TABLE, check_frame_size,
                  check_settings_max_frame_size, entry_size, hpack_decode_int,
                  hpack_encode_int, pack_frame, unpack_frame)

PASS = [0]


def ok(name, cond, extra=""):
    if cond:
        PASS[0] += 1
        print(f"  PASS  {name}{(' -> ' + extra) if extra else ''}")
    else:
        raise AssertionError(f"FAIL  {name}{(' -> ' + extra) if extra else ''}")


print("== 1. §4.1 帧头：9 字节，Length 不含帧头 ==")
raw = pack_frame(0x0, 0x01, 1, b"hello")
ok("帧头恰好 9 字节 + payload", len(raw) == 14, str(len(raw)))
length, ftype, flags, sid, payload = unpack_frame(raw)
ok("Length 只算 payload = 5", length == 5, str(length))
ok("Type/Flags/StreamID 往返一致",
   (ftype, flags, sid, payload) == (0x0, 0x01, 1, b"hello"),
   f"{ftype} {flags} {sid} {payload}")
ok("DATA 的类型码是 0x0", FRAME_TYPES[ftype] == "DATA")
raw = pack_frame(0x8, 0x00, 0, b"\x00\x00\x10\x00")
length, _, _, sid, _ = unpack_frame(raw)
ok("Stream ID 0 表示作用于整条连接", sid == 0 and length == 4)
raw = pack_frame(0x1, 0x0, 0x7FFFFFFF, b"")
ok("Stream ID 是 31 位（高位被 Reserved 位占据）", unpack_frame(raw)[3] == 0x7FFFFFFF)
try:
    pack_frame(0x1, 0x0, 1 << 31, b"")
    ok("Stream ID 超 31 位应报错", False)
except ValueError:
    ok("Stream ID 超 31 位抛 ValueError", True)
try:
    unpack_frame(b"\x00\x00\x05\x00\x00\x00\x00\x00")
    ok("帧头不足 9 字节应报 FRAME_SIZE_ERROR", False)
except ConnectionError as e:
    ok("帧头不足 9 字节 → FRAME_SIZE_ERROR", e.code == "FRAME_SIZE_ERROR", e.code)

print("== 2. §4.2 帧大小上限 ==")
check_frame_size(16384, 16384)
ok("恰好等于 MAX_FRAME_SIZE 可通过", True)
try:
    check_frame_size(16385, 16384)
    ok("超过上限应报错", False)
except ConnectionError as e:
    ok("超过 MAX_FRAME_SIZE → FRAME_SIZE_ERROR", e.code == "FRAME_SIZE_ERROR")
for bad in (16383, 16777216):
    try:
        check_settings_max_frame_size(bad)
        ok(f"MAX_FRAME_SIZE={bad} 应越界", False)
    except ConnectionError as e:
        ok(f"MAX_FRAME_SIZE={bad} 越界 → PROTOCOL_ERROR", e.code == "PROTOCOL_ERROR")
ok("合法区间是 [2^14, 2^24-1]",
   (MAX_FRAME_SIZE_MIN, MAX_FRAME_SIZE_MAX) == (16384, 16777215))

print("== 3. §6.5.2 SETTINGS 初始值 ==")
ok("HEADER_TABLE_SIZE 初始 4096", INITIAL_VALUES[SETTINGS_HEADER_TABLE_SIZE] == 4096)
ok("ENABLE_PUSH 初始 1", INITIAL_VALUES[SETTINGS_ENABLE_PUSH] == 1)
ok("MAX_CONCURRENT_STREAMS 初始无限制",
   INITIAL_VALUES[SETTINGS_MAX_CONCURRENT_STREAMS] is None)
ok("INITIAL_WINDOW_SIZE 初始 65535 = 2^16-1",
   INITIAL_VALUES[SETTINGS_INITIAL_WINDOW_SIZE] == 65535)
ok("MAX_FRAME_SIZE 初始 16384 = 2^14",
   INITIAL_VALUES[SETTINGS_MAX_FRAME_SIZE] == 16384)
ok("MAX_HEADER_LIST_SIZE 初始无限制",
   INITIAL_VALUES[SETTINGS_MAX_HEADER_LIST_SIZE] is None)

print("== 4. §6.9.2 流控：官方的 −44 KB 例子 ==")
fc = FlowControl(initial_window=65535)
fc.open_stream(1)
sent = 0
while sent < 60 * 1024:                       # 客户端建连后立刻发 60 KB
    n = fc.send(1, 60 * 1024 - sent)
    if n == 0:
        break
    sent += n
ok("60 KB 全部发出（默认窗口 65535 够用），流窗口剩 4095",
   sent == 61440 and fc.stream_window[1] == 4095,
   f"{sent} {fc.stream_window[1]}")
fc.apply_initial_window_change(16 * 1024)     # 服务端把初始窗口改成 16 KB
# 规范口径：按「新旧初始值之差」调整已有窗口 → 4095 + (16384 − 65535) = −45056
ok("按差值调整后流窗口 = 16 KB − 60 KB = −44 KB（官方例子的数）",
   fc.stream_window[1] == -44 * 1024, str(fc.stream_window[1]))
ok("该值等于 4095 + (16384 − 65535)", 4095 + (16 * 1024 - 65535) == -44 * 1024)
ok("窗口为负时不能再发送", fc.send(1, 1) == 0)

print("== 5. §6.9.2 连接窗口只能靠 WINDOW_UPDATE 改 ==")
fc = FlowControl(initial_window=65535)
fc.open_stream(1)
fc.send(1, 1000)
conn_before = fc.connection_window
fc.apply_initial_window_change(32768)
ok("SETTINGS 不改变连接窗口", fc.connection_window == conn_before,
   f"{fc.connection_window} {conn_before}")
ok("SETTINGS 按差值改流窗口（65535-1000 再加 -32767）",
   fc.stream_window[1] == 65535 - 1000 - 32767, str(fc.stream_window[1]))
fc.window_update(None, 1000)
ok("WINDOW_UPDATE 能改连接窗口", fc.connection_window == conn_before + 1000)
try:
    fc.apply_initial_window_change(1 << 31)
    ok("INITIAL_WINDOW_SIZE 超 2^31-1 应报错", False)
except ConnectionError as e:
    ok("INITIAL_WINDOW_SIZE 超 2^31-1 → FLOW_CONTROL_ERROR",
       e.code == "FLOW_CONTROL_ERROR", e.code)

print("== 6. HPACK §5.1 整数表示：附录 C.1 官方向量 ==")
ok("10 用 5 位前缀 → 0x0A", hpack_encode_int(10, 5) == b"\x0a",
   hpack_encode_int(10, 5).hex())
ok("1337 用 5 位前缀 → 1F 9A 0A", hpack_encode_int(1337, 5) == b"\x1f\x9a\x0a",
   hpack_encode_int(1337, 5).hex())
ok("42 用 8 位前缀 → 0x2A", hpack_encode_int(42, 8) == b"\x2a",
   hpack_encode_int(42, 8).hex())
ok("31 用 5 位前缀仍单字节（< 2^5-1 的边界是 30）",
   hpack_encode_int(30, 5) == b"\x1e" and hpack_encode_int(31, 5) == b"\x1f\x00",
   hpack_encode_int(31, 5).hex())
for v in (0, 1, 30, 31, 127, 128, 1337, 65535, 1 << 20):
    for bits in (5, 6, 7, 8):
        enc = hpack_encode_int(v, bits)
        dec, pos = hpack_decode_int(enc, 0, bits)
        assert dec == v and pos == len(enc), (v, bits, enc.hex(), dec, pos)
ok("5/6/7/8 位前缀下 9 个值全部往返一致且吃满字节", True)
ok("前缀全 1 是多字节续接的标志（0x1F 后必跟续字节）",
   len(hpack_encode_int(1337, 5)) == 3)

print("== 7. RFC 7541 附录 C.3.1：首个请求的官方字节序列 ==")
wire = bytes.fromhex("82868441" "0f7777772e6578616d706c652e636f6d")
ok("官方 hex 共 20 字节（4 个索引/字面量头 + 15 字节主机名）",
   len(wire) == 20, str(len(wire)))
dec = HpackDecoder()
headers = dec.decode(wire)
ok("解码出 4 条头字段", len(headers) == 4, str(headers))
ok("内容与官方一致",
   headers == [(":method", "GET"), (":scheme", "http"), (":path", "/"),
               (":authority", "www.example.com")], str(headers))
ok("0x82 → 静态表索引 2 是 :method GET", STATIC_TABLE[1] == (":method", "GET"))
ok("0x86 → 索引 6 是 :scheme http", STATIC_TABLE[5] == (":scheme", "http"))
ok("0x84 → 索引 4 是 :path /", STATIC_TABLE[3] == (":path", "/"))
ok("0x41 → 带索引字面量，名字取索引 1 的 :authority",
   dec.lookup(1)[0] == ":authority")
ok("动态表新增 1 条，正是 :authority: www.example.com",
   dec.dynamic == [(":authority", "www.example.com")], str(dec.dynamic))

print("== 8. 动态表条目大小：官方标注 s=57 ==")
ok("条目额外开销是 32 字节", ENTRY_OVERHEAD == 32)
ok(":authority(10) + www.example.com(15) + 32 = 57",
   entry_size(":authority", "www.example.com") == 57,
   str(entry_size(":authority", "www.example.com")))
ok("解码器当前大小与官方一致", dec.size == 57, str(dec.size))

print("== 9. 动态表驱逐：超出 SETTINGS_HEADER_TABLE_SIZE 即淘汰最旧 ==")
d = HpackDecoder(max_size=100)
d.add("a", "b")                      # 1+1+32 = 34
ok("单条后 size=34", d.size == 34, str(d.size))
d.add("cc", "dd")                    # +36 → 70
ok("两条后 size=70", d.size == 70, str(d.size))
d.add("e", "f")                      # +34 → 104 > 100，淘汰最旧
ok("超限时淘汰最旧一条，回到 70", d.size == 70 and len(d.dynamic) == 2,
   f"{d.size} {len(d.dynamic)}")
ok("保留的是后两条", d.dynamic[0] == ("e", "f") and d.dynamic[1] == ("cc", "dd"),
   str(d.dynamic))

print("== 10. 静态表边界与非法索引 ==")
ok("静态表共 61 项", len(STATIC_TABLE) == 61, str(len(STATIC_TABLE)))
ok("末项索引 61 是 www-authenticate", STATIC_TABLE[60] == ("www-authenticate", ""))
d = HpackDecoder()
try:
    d.lookup(0)
    ok("索引 0 非法", False)
except ConnectionError as e:
    ok("索引 0 → PROTOCOL_ERROR", e.code == "PROTOCOL_ERROR")
try:
    d.lookup(62)
    ok("越界索引应报错", False)
except ConnectionError as e:
    ok("动态表为空时索引 62 越界 → PROTOCOL_ERROR", e.code == "PROTOCOL_ERROR")

print(f"\n全部 {PASS[0]} 项断言通过")
