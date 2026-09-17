# -*- coding: utf-8 -*-
"""pb_check.py — 自检:逐条核对 protobuf.dev 官方编码文档里的明文规则。"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pb_varint import (  # noqa: E402
    MAX_FIELD_NUMBER, MAX_VARINT_BYTES, MIN_FIELD_NUMBER, RESERVED_FIELD_HI,
    RESERVED_FIELD_LO, WIRE_EGROUP, WIRE_I32, WIRE_I64, WIRE_LEN, WIRE_SGROUP,
    WIRE_VARINT, ProtobufError, as_signed64, decode_tag, decode_varint,
    encode_tag, encode_varint, tag_size, validate_field_number, varint_length,
    zigzag_decode, zigzag_encode,
)
from pb_wire import (  # noqa: E402
    KIND_TO_WIRE, FieldDef, MessageDef, decode_message, encode_message,
    encode_record, encode_scalar, iter_records, merge_message, parse_group,
    skip_field, wire_name,
)

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS ", label)
        return
    failed += 1
    print("  FAIL ", label, detail)


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except (ProtobufError, ValueError):
        return True


def main():
    print("[1] Base 128 Varints(官方:7 位 payload,低位在前)")
    check("1 编码为 01", encode_varint(1).hex() == "01")
    check("150 编码为 9601", encode_varint(150).hex() == "9601")
    check("150 的首字节 MSB 置位(还有后续字节)",
          encode_varint(150)[0] & 0x80 == 0x80)
    check("150 的次字节 MSB 清零(已到末尾)",
          encode_varint(150)[1] & 0x80 == 0)
    payloads = [b & 0x7F for b in encode_varint(150)]
    check("低位在前:0x16 + (0x01 << 7) == 150",
          payloads[0] + (payloads[1] << 7) == 150, str(payloads))
    check("varint 往返:0/1/127/128/300/2^40",
          all(decode_varint(encode_varint(v))[0] == v
              for v in (0, 1, 127, 128, 300, 1 << 40)))
    check("1 字节上界 127 → 1 字节", varint_length(127) == 1)
    check("128 需要 2 字节", varint_length(128) == 2)
    check("2^63-1 → 9 字节", varint_length((1 << 63) - 1) == 9)
    check("2^64-1 → 10 字节(官方上限)", varint_length((1 << 64) - 1) == MAX_VARINT_BYTES)
    check("超过 10 字节的 varint 视为损坏",
          raises(decode_varint, b"\xff" * 11))
    check("截断的 varint 视为损坏", raises(decode_varint, b"\x96"))
    check("解码返回正确的新位置",
          decode_varint(encode_varint(300) + b"zz")[1] == len(encode_varint(300)))

    print("[2] tag = (field_number << 3) | wire_type(官方公式)")
    check("字段 1 + VARINT → 0x08", encode_tag(1, WIRE_VARINT).hex() == "08")
    check("字段 2 + LEN → 0x12", encode_tag(2, WIRE_LEN).hex() == "12")
    check("字段 3 + I32 → 0x1d", encode_tag(3, WIRE_I32).hex() == "1d")
    number, wire, pos = decode_tag(b"\x08")
    check("解码 0x08 → 字段 1 / wire type 0 / 位置 1",
          (number, wire, pos) == (1, WIRE_VARINT, 1))
    raw = decode_varint(b"\x08")[0]
    check("低 3 位就是 wire type", raw & 0x07 == WIRE_VARINT)
    check("右移 3 位就是字段号", raw >> 3 == 1)
    check("字段号下界是 1", MIN_FIELD_NUMBER == 1)
    check("字段号上界是 536870911(官方 2^29-1)", MAX_FIELD_NUMBER == 536_870_911)
    check("字段号 0 不合法", raises(validate_field_number, 0))
    check("字段号超上界不合法", raises(validate_field_number, MAX_FIELD_NUMBER + 1))
    check("字段号上界本身合法", validate_field_number(MAX_FIELD_NUMBER) is None)
    check("19000..19999 被官方保留",
          (RESERVED_FIELD_LO, RESERVED_FIELD_HI) == (19000, 19999)
          and raises(validate_field_number, 19000)
          and raises(validate_field_number, 19999))
    check("19999 之外已可用", validate_field_number(20000) is None)
    check("大字段号 tag 仍可往返",
          decode_tag(encode_tag(MAX_FIELD_NUMBER, WIRE_LEN))[:2] == (MAX_FIELD_NUMBER, WIRE_LEN))
    check("tag 长度随字段号增长", tag_size(15, WIRE_VARINT) == 1 and tag_size(16, WIRE_VARINT) == 2)

    print("[3] 官方示例:message Test1 { int32 a = 1; } a = 150")
    test1 = MessageDef("Test1", [FieldDef(1, "a", "int32")])
    blob = encode_message(test1, {"a": 150})
    check("序列化结果是 08 96 01", blob.hex() == "089601", blob.hex())
    check("长度 3 字节", len(blob) == 3)
    check("反解回 {a: 150}", decode_message(test1, blob) == {"a": 150})
    check("可以在 TLV 上读出 1:VARINT 150",
          [(n, wire_name(w), decode_varint(p)[0]) for n, w, p in iter_records(blob)]
          == [(1, "VARINT", 150)])

    print("[4] 六种 wire type(官方表)")
    check("VARINT=0 I64=1 LEN=2 SGROUP=3 EGROUP=4 I32=5",
          (WIRE_VARINT, WIRE_I64, WIRE_LEN, WIRE_SGROUP, WIRE_EGROUP, WIRE_I32)
          == (0, 1, 2, 3, 4, 5))
    want = {"int32": 0, "int64": 0, "uint32": 0, "sint32": 0, "sint64": 0,
            "bool": 0, "enum": 0, "fixed64": 1, "double": 1, "sfixed64": 1,
            "string": 2, "bytes": 2, "message": 2,
            "fixed32": 5, "float": 5, "sfixed32": 5}
    check("16 种标量类型全部映射正确",
          all(KIND_TO_WIRE[k] == v for k, v in want.items()), str(KIND_TO_WIRE))
    check("wire type 名称可读", wire_name(WIRE_LEN) == "LEN")

    print("[5] 非 varint 数字:定长块")
    check("double 走 I64,8 字节",
          FieldDef(1, "d", "double").wire_type() == WIRE_I64
          and len(encode_scalar(FieldDef(1, "d", "double"), 25.4)) == 8)
    check("float 走 I32,4 字节",
          FieldDef(1, "f", "float").wire_type() == WIRE_I32
          and len(encode_scalar(FieldDef(1, "f", "float"), 25.4)) == 4)
    check("fixed64 是 8 字节小端",
          encode_scalar(FieldDef(1, "x", "fixed64"), 200) == (200).to_bytes(8, "little"))
    check("double 用 IEEE754 双精度",
          encode_scalar(FieldDef(1, "d", "double"), 25.4) == struct.pack("<d", 25.4))
    check("float 用 IEEE754 单精度",
          encode_scalar(FieldDef(1, "f", "float"), 25.4) == struct.pack("<f", 25.4))
    m5 = MessageDef("M", [FieldDef(5, "d", "double")])
    check("double 记录 = tag(1) + 8", len(encode_message(m5, {"d": 25.4})) == 9)

    print("[6] 负数:补码 vs ZigZag(官方对照表)")
    check("int 负数用补码且占满 10 字节",
          encode_varint(-2).hex() == "feffffffffffffffff01", encode_varint(-2).hex())
    check("-2 的 varint_length 是 10", varint_length(-2) == MAX_VARINT_BYTES)
    check("as_signed64 还原 -2", as_signed64(decode_varint(encode_varint(-2))[0]) == -2)
    table = [(0, 0), (-1, 1), (1, 2), (-2, 3),
             (0x7FFFFFFF, 0xFFFFFFFE), (-0x80000000, 0xFFFFFFFF)]
    check("ZigZag 官方对照表逐行吻合",
          all(zigzag_encode(n, 32) == e for n, e in table),
          str([(n, zigzag_encode(n, 32)) for n, _ in table]))
    check("ZigZag 往返 -1/1/-2/2/极值",
          all(zigzag_decode(zigzag_encode(n)) == n
              for n in (-1, 1, -2, 2, 0x7FFFFFFF, -0x80000000, 0)))
    check("官方示例 -500 → 999", zigzag_encode(-500) == 999 and zigzag_decode(999) == -500)
    check("sint 负数只占 2 字节,远短于 int 的 10 字节",
          len(encode_varint(zigzag_encode(-500))) < varint_length(-500))
    ms = MessageDef("M", [FieldDef(1, "a", "int32"), FieldDef(2, "b", "sint32")])
    a_len = len(encode_message(ms, {"a": -2}))
    b_len = len(encode_message(ms, {"b": -2}))
    check("同一负数 sint32 编码明显更短", b_len < a_len, "%d vs %d" % (b_len, a_len))

    print("[7] Length-Delimited:长度前缀紧跟 tag")
    m7 = MessageDef("M", [FieldDef(4, "s", "string")])
    b7 = encode_message(m7, {"s": "hello"})
    check("string 记录 = tag + len(varint) + 5 字节",
          len(b7) == 1 + 1 + 5 and b7[0] == 0x22, b7.hex())
    check("长度前缀就是 payload 长度", b7[1] == 5)
    check("string 往返", decode_message(m7, b7) == {"s": "hello"})
    check("UTF-8 多字节字符按字节计长度",
          len(encode_message(m7, {"s": "中文"})) == 1 + 1 + 6)
    inner = MessageDef("Inner", [FieldDef(1, "x", "int32")])
    outer = MessageDef("Outer", [FieldDef(1, "in", "message", msg_def=inner)])
    b8 = encode_message(outer, {"in": {"x": 150}})
    check("嵌套消息 = 外层 LEN 包住内层字节",
          b8.hex() == "0a03089601", b8.hex())
    check("嵌套消息往返", decode_message(outer, b8) == {"in": {"x": 150}})

    print("[8] packed 重复字段 vs 非 packed")
    packed = MessageDef("P", [FieldDef(5, "nums", "int32", repeated=True)])
    unpacked = MessageDef("U", [FieldDef(5, "nums", "int32", repeated=True, packed=False)])
    bp = encode_message(packed, {"nums": [1, 2, 3]})
    bu = encode_message(unpacked, {"nums": [1, 2, 3]})
    check("packed → 单条 LEN 记录", len(list(iter_records(bp))) == 1)
    check("非 packed → 三条记录", len(list(iter_records(bu))) == 3)
    check("packed 的 payload 是三个 varint 首尾相连",
          bp == encode_record(5, WIRE_LEN, b"\x01\x02\x03"))
    check("两种形式解码结果一致",
          decode_message(packed, bp) == decode_message(unpacked, bp) == {"nums": [1, 2, 3]})
    check("非 packed 字节也能被 packed 声明的解析器接受",
          decode_message(packed, bu) == {"nums": [1, 2, 3]})
    check("官方:5:{1 2} 4:{\"hello\"} 5:{3} 必须被接受",
          decode_message(
              MessageDef("M", [FieldDef(4, "s", "string"),
                               FieldDef(5, "nums", "int32", repeated=True)]),
              encode_record(5, WIRE_LEN, b"\x01\x02") + encode_record(4, WIRE_LEN, b"hello")
              + encode_record(5, WIRE_LEN, b"\x03"),
          ) == {"s": "hello", "nums": [1, 2, 3]})

    print("[9] 未知字段必须被跳过(前向兼容)")
    old = MessageDef("Old", [FieldDef(1, "a", "int32")])
    new = MessageDef("New", [FieldDef(1, "a", "int32"), FieldDef(2, "s", "string"),
                             FieldDef(3, "d", "double")])
    nb = encode_message(new, {"a": 7, "s": "x", "d": 1.5})
    check("旧 schema 读新消息不报错且拿到已知字段",
          decode_message(old, nb) == {"a": 7}, str(decode_message(old, nb)))
    check("新 schema 读全部字段", decode_message(new, nb) == {"a": 7, "s": "x", "d": 1.5})
    check("skip_field 对 VARINT 正确", skip_field(b"\x01", 0, WIRE_VARINT) == 1)
    check("skip_field 对 I64 正确", skip_field(b"\x00" * 8, 0, WIRE_I64) == 8)
    check("skip_field 对 I32 正确", skip_field(b"\x00" * 4, 0, WIRE_I32) == 4)
    check("skip_field 对 LEN 正确", skip_field(b"\x05hello", 0, WIRE_LEN) == 6)

    print("[10] Last One Wins 与嵌入消息合并(官方原文)")
    m10 = MessageDef("M", [FieldDef(1, "a", "int32")])
    twice = encode_record(1, WIRE_VARINT, b"\x01") + encode_record(1, WIRE_VARINT, b"\x02")
    check("标量出现两次 → 取最后一个值", decode_message(m10, twice) == {"a": 2})
    inner10 = MessageDef("I", [FieldDef(1, "x", "int32"), FieldDef(2, "y", "int32")])
    outer10 = MessageDef("O", [FieldDef(1, "m", "message", msg_def=inner10)])
    merged = (encode_record(1, WIRE_LEN, encode_message(inner10, {"x": 1, "y": 2}))
              + encode_record(1, WIRE_LEN, encode_message(inner10, {"x": 9})))
    check("嵌入消息出现两次 → 递归合并(后者覆盖标量)",
          decode_message(outer10, merged) == {"m": {"x": 9, "y": 2}},
          str(decode_message(outer10, merged)))
    check("merge_message 对 repeated 做拼接",
          merge_message({"r": [1]}, {"r": [2, 3]},
                        MessageDef("M", [FieldDef(1, "r", "int32", repeated=True)]))
          == {"r": [1, 2, 3]})

    print("[11] 字段顺序:字节不稳定,语义稳定(官方 Implications)")
    m11 = MessageDef("M", [FieldDef(1, "a", "int32"), FieldDef(2, "b", "int32")])
    fwd = encode_message(m11, {"a": 1, "b": 2})
    rev = encode_message(m11, {"a": 1, "b": 2}, field_order=["b", "a"])
    check("交换字段顺序后字节不同", fwd != rev, "%s vs %s" % (fwd.hex(), rev.hex()))
    check("但语义相同", decode_message(m11, fwd) == decode_message(m11, rev) == {"a": 1, "b": 2})
    check("声明顺序不影响序列化:反序也能解析",
          decode_message(m11, rev) == {"a": 1, "b": 2})

    print("[12] group:SGROUP/EGROUP 字段号必须配对")
    check("group 记录 payload 为空",
          encode_record(8, WIRE_SGROUP, b"") == encode_tag(8, WIRE_SGROUP)
          and encode_record(8, WIRE_EGROUP, b"") == encode_tag(8, WIRE_EGROUP))
    body = encode_record(1, WIRE_VARINT, b"\x02")
    blob = body + encode_tag(8, WIRE_EGROUP)
    payload, end = parse_group(blob, 0, 8)
    check("配对时能取出 group 内容", payload == body and end == len(blob))
    bad = body + encode_tag(7, WIRE_EGROUP)
    check("遇到 7:EGROUP 而期望 8:EGROUP → mal-formed", raises(parse_group, bad, 0, 8))
    check("缺少 EGROUP 也是损坏", raises(parse_group, body, 0, 8))

    print("")
    print("断言总数 %d,失败 %d" % (passed + failed, failed))
    if failed:
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
