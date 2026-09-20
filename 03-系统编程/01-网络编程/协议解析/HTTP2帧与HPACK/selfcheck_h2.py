# -*- coding: utf-8 -*-
"""HTTP/2 帧 + HPACK —— 自检

判据取自 RFC 9113 §4.1/§4.2 与 RFC 7541 §4/§5/§6 的 MUST / SHOULD 原文。
负向判据重点在「越界与边界」：strictly less than 2^N-1、索引 0、未知帧类型。
"""
from h2_frame import *  # noqa: F401,F403
from hpack import *     # noqa: F401,F403

N = 0
FAILED = []


def check(label, cond, detail=""):
    global N
    N += 1
    if cond:
        print("ok   %-58s %s" % (label, detail))
    else:
        FAILED.append(label)
        print("FAIL %-58s %s" % (label, detail))


print("== 1. 帧头 9 字节，Length 不含帧头 ==")
f = encode_frame(HEADERS, 0x04, 1, b"abc")
check("帧总长 = 9 + payload", len(f) == 12, "len=%d" % len(f))
fr, pos = decode_frame(f)
check("解出的 Length 是 payload 长度而非总长", fr.length == 3 and pos == 12, "length=%d" % fr.length)
check("帧头本身占 9 字节", HEADER_LEN == 9)
check("Type/Flags/StreamID 正确还原",
      fr.type == HEADERS and fr.flags == 0x04 and fr.stream_id == 1)

print("\n== 2. 保留位与流 ID ==")
f2 = encode_frame(DATA, 0x01, 0x7FFFFFFF, b"x")
fr2, _ = decode_frame(f2)
check("流 ID 是 31 位，上限 0x7FFFFFFF", fr2.stream_id == 0x7FFFFFFF, "sid=%d" % fr2.stream_id)
check("发送时保留位为 0", fr2.reserved == 0)
f3 = encode_frame(DATA, 0x01, 7, b"x", reserved=1)
fr3, _ = decode_frame(f3)
check("收到保留位=1 时 MUST be ignored（仍能解出流 ID）",
      fr3.reserved == 1 and fr3.stream_id == 7, "reserved=%d sid=%d" % (fr3.reserved, fr3.stream_id))
f4 = encode_frame(SETTINGS, 0x00, 0, b"")
fr4, _ = decode_frame(f4)
check("流 ID 0 保留给连接级帧", fr4.stream_id == 0)

print("\n== 3. 未知帧类型必须忽略并丢弃 ==")
f5 = encode_frame(0xFE, 0x00, 3, b"junk")
fr5, pos5 = decode_frame(f5)
check("类型 0xFE 不在已知表内", fr5.name.startswith("UNKNOWN"), fr5.name)
check("解出的 payload 长度仍然正确（丢弃前先读长度）", fr5.length == 4 and pos5 == 13, "length=%d" % fr5.length)
check("仅 DATA/SETTINGS 等 10 种为已知类型", len(FRAME_NAMES) == 10, "n=%d" % len(FRAME_NAMES))

print("\n== 4. 未定义 flags 必须被忽略 ==")
f6 = encode_frame(DATA, 0x40, 1, b"y")       # 0x40 对 DATA 未定义
fr6, _ = decode_frame(f6)
check("DATA 带 0x40 仍能正常解码", fr6.flags == 0x40 and fr6.length == 1)

print("\n== 5. 帧大小上限 ==")
check("默认上限 2^14 = 16384", DEFAULT_MAX_FRAME == 16384)
check("SETTINGS_MAX_FRAME_SIZE 下界 2^14 上界 2^24-1",
      MIN_MAX_FRAME == 16384 and MAX_MAX_FRAME == 16777215)
check("payload 16384 在默认上限内", check_frame_size(16384, DEFAULT_MAX_FRAME))
check("负向：payload 16385 超出默认上限", not check_frame_size(16385, DEFAULT_MAX_FRAME))
check("协商放大到 32768 后 16385 合法", check_frame_size(16385, 32768))

print("\n== 6. HPACK 整数：边界是 strictly less than 2^N-1 ==")
check("126 < 127 → 单字节 0xFE", encode_int(126, 7, INDEXED) == b"\xfe",
      repr(encode_int(126, 7, INDEXED)))
enc127 = encode_int(127, 7, INDEXED)
check("127 不小于 127 → 走双字节 0xFF 0x00（易错点）",
      enc127 == b"\xff\x00", repr(enc127))
check("127 解回仍是 127", decode_int(enc127, 0, 7)[0] == 127)
check("128 → 0xFF 0x01", encode_int(128, 7, INDEXED) == b"\xff\x01",
      repr(encode_int(128, 7, INDEXED)))
for v in (0, 1, 10, 126, 127, 128, 1337, 65535, 1 << 20):
    e = encode_int(v, 7, INDEXED)
    got, np = decode_int(e + b"\x00", 0, 7)
    check("往返一致 v=%d" % v, got == v and np == len(e), "%r" % e)
check("前缀位 5 位（动态表更新）时 30 是单字节",
      encode_int(30, 5, SIZE_UPDATE) == b"\x3e", repr(encode_int(30, 5, SIZE_UPDATE)))
check("前缀位 5 位时 31 走双字节", encode_int(31, 5, SIZE_UPDATE) == b"\x3f\x00",
      repr(encode_int(31, 5, SIZE_UPDATE)))

print("\n== 7. 静态表 ==")
check("静态表共 61 项", STATIC_SIZE == 61, "n=%d" % STATIC_SIZE)
ctx = Context()
check("索引 2 = (:method, GET)", ctx.lookup(2) == (":method", "GET"), str(ctx.lookup(2)))
check("索引 8 = (:status, 200)", ctx.lookup(8) == (":status", "200"))
check("索引 61 = www-authenticate", ctx.lookup(61) == ("www-authenticate", ""))
check("索引 16 的 accept-encoding 带值 gzip, deflate",
      ctx.lookup(16) == ("accept-encoding", "gzip, deflate"))
try:
    ctx.lookup(0)
    check("负向：索引 0 必须报错", False, "没有抛异常")
except HPackError:
    check("负向：索引 0 必须报错", True, "HPackError")

print("\n== 8. 索引 0 的 indexed 表示是解码错误 ==")
try:
    decode(Context(), encode_int(0, 7, INDEXED))
    check("负向：indexed 索引 0 必须判为解码错误", False, "没有抛异常")
except HPackError:
    check("负向：indexed 索引 0 必须判为解码错误", True, "HPackError")

print("\n== 9. 条目大小与动态表寻址 ==")
check("条目大小 = name+value+32", entry_size("host", "a.io") == 4 + 4 + 32 == 40,
      "%d" % entry_size("host", "a.io"))
c = Context(max_size=4096)
c.add("x-token", "v1")
check("新增后动态索引 62 指向最新条目",
      c.find("x-token", "v1") == 62 and c.lookup(62) == ("x-token", "v1"),
      "idx=%s" % c.find("x-token", "v1"))
check("表内字节数 = 7+2+32 = 41", c.size == 41, "size=%d" % c.size)

print("\n== 10. 三种字面量表示对动态表的影响 ==")
c1 = Context(max_size=4096)
encode_literal(c1, "no-index", "a", mode="none")
check("without indexing 不进动态表", len(c1.dyn) == 0 and c1.size == 0)
c2 = Context(max_size=4096)
encode_literal(c2, "never", "a", mode="never")
check("never indexed 不进动态表", len(c2.dyn) == 0 and c2.size == 0)
c3 = Context(max_size=4096)
encode_literal(c3, "incr", "a", mode="incremental")
check("incremental indexing 进动态表", len(c3.dyn) == 1 and c3.size == 4 + 1 + 32)

print("\n== 11. 逐出规则 ==")
ce = Context(max_size=100)
ce.add("a", "b")            # 34
ce.add("c", "d")            # 68
check("两条 34 字节条目共存（68 <= 100）", len(ce.dyn) == 2 and ce.size == 68,
      "size=%d" % ce.size)
ce.add("e", "f")            # 需腾到 66 → 逐出最旧的 a
check("新增前先逐出到 size <= max - new", len(ce.dyn) == 2 and ce.size == 68,
      "size=%d" % ce.size)
check("被逐出的是最旧条目 a（尾部）",
      ce.dyn[-1] == ("c", "d") and ce.dyn[0] == ("e", "f"), str(ce.dyn))
check("a 已不在表中", ce.find("a", "b") is None)

cb = Context(max_size=100)
big = "n" * 5000
ok_add = cb.add(big, "v")
check("负向：大于 max 的条目无法插入", ok_add is False)
check("尝试插入过大条目会把整表清空", cb.dyn == [] and cb.size == 0,
      "size=%d" % cb.size)

cs = Context(max_size=1000)
cs.add("k", "v")
cs.add("k2", "v2")
encode_size_update(cs, 0)
check("动态表大小更新为 0 → 全表逐出", cs.dyn == [] and cs.size == 0)

print("\n== 12. 端到端：编码再解码 ==")
enc_ctx = Context(max_size=4096)
blk = b""
blk += encode_indexed(enc_ctx, 2)                       # :method GET
blk += encode_indexed(enc_ctx, 7)                       # :scheme https
blk += encode_indexed(enc_ctx, 4)                       # :path /
blk += encode_literal(enc_ctx, "custom-key", "v", mode="incremental")
dec_ctx = Context(max_size=4096)
got = decode(dec_ctx, blk)
check("解出 4 个头部", len(got) == 4, str(got[:2]))
check("静态索引还原正确",
      got[0] == (":method", "GET") and got[1] == (":scheme", "https")
      and got[2] == (":path", "/"), str(got[:3]))
check("字面量正确", got[3] == ("custom-key", "v"), str(got[3]))
check("解码侧动态表与编码侧同步", len(dec_ctx.dyn) == 1 and dec_ctx.dyn[0] == ("custom-key", "v"))

print("\n== 13. 第二次请求复用动态表：压缩生效 ==")
enc_ctx2 = Context(max_size=4096)
encode_literal(enc_ctx2, "cookie", "sid=8", mode="incremental")
blk2 = encode_indexed(enc_ctx2, enc_ctx2.find("cookie", "sid=8"))
check("第二次只用一个索引字节", len(blk2) == 1 and blk2[0] & INDEXED, repr(blk2))
dec_ctx2 = Context(max_size=4096)
decode(dec_ctx2, encode_literal(Context(max_size=4096), "cookie", "sid=8"))
check("解码侧同样能靠索引还原", decode(dec_ctx2, blk2) == [("cookie", "sid=8")])

print("\n== 14. 字符串字面量：H 位与长度口径 ==")
s = encode_str(b"hello", huffman=False)
d, h, np = decode_str(s, 0)
check("H=0 时原样返回", d == b"hello" and h is False and np == len(s), repr(s))
s2 = bytes([0x80 | 3]) + b"abc"
d2, h2, _ = decode_str(s2, 0)
check("H=1 被识别（Huffman 码表不在本 demo 实现范围）", h2 is True and d2 == b"abc")
check("长度字段计的是编码后字节数", s[0] == 5 and len(s) == 6, repr(s))

print("\n---- %d 项断言，失败 %d 项 ----" % (N, len(FAILED)))
if FAILED:
    for f in FAILED:
        print("  FAILED:", f)
    raise SystemExit(1)
print("ALL PASS")
