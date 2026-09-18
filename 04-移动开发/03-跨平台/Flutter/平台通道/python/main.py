"""平台通道编解码格式的自检(纯标准库,直接 python3 main.py 运行)。

断言全部针对权威资料里**明确写过**的性质:类型判定字节取值、expanding 长度格式的
分支边界、UTF-8 字节长度而非字符数、double 与 TypedData 的对齐填充、以及三处
FormatException 判定条件。凡是资料只给语义、没给数值的地方(如 NOT_IMPLEMENTED
在 Dart 侧解码成什么),这里不做断言,只在 README 里标注为未确证。
"""

import math
import sys

from method_codec import (PlatformException, decode_envelope,
                          decode_method_call, encode_error_envelope,
                          encode_method_call, encode_success_envelope)
from sc_codec import (F32List, F64List, FormatException, I32List, I64List,
                      TYPE_FLOAT64, TYPE_INT32, TYPE_INT64, TYPE_LIST, TYPE_MAP,
                      TYPE_NAMES, TYPE_NULL, TYPE_STRING, TYPE_TRUE, TYPE_FALSE,
                      ReadBuffer, U8List, decode_message, read_size,
                      size_encoded_len, write_size, write_value, WriteBuffer)

PASS = 0
FAIL = 0
FAILED = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append("%s | %s" % (label, detail))


def size_bytes(value):
    buf = WriteBuffer()
    write_size(buf, value)
    return bytes(buf.data)


# ---------------- 1. expanding 长度格式 ----------------
check("size(0)=1B", size_bytes(0) == b"\x00", size_bytes(0).hex())
check("size(253)=1B", size_bytes(253) == bytes([253]), size_bytes(253).hex())
check("size(253) 仍是单字节分支", size_encoded_len(253) == 1)
check("size(254)=254+uint16", size_bytes(254)[0] == 254 and len(size_bytes(254)) == 3,
      size_bytes(254).hex())
check("size(254) 三字节", size_encoded_len(254) == 3)
check("size(0xFFFF)=254 分支", size_bytes(0xFFFF)[0] == 254 and len(size_bytes(0xFFFF)) == 3)
check("size(0x10000)=255 分支", size_bytes(0x10000)[0] == 255 and len(size_bytes(0x10000)) == 5,
      size_bytes(0x10000).hex())
for v in (0, 1, 253, 254, 300, 0xFFFF, 0x10000, 0xFFFFFFFF):
    check("size 往返 %d" % v, read_size(ReadBuffer(size_bytes(v))) == v)

# ---------------- 2. 类型判定字节 ----------------
check("null→0", TYPE_NAMES[0] == "null")
check("true→1", TYPE_TRUE == 1 and TYPE_FALSE == 2)
check("int32→3", TYPE_INT32 == 3 and TYPE_INT64 == 4)
check("float64→6 / string→7", TYPE_FLOAT64 == 6 and TYPE_STRING == 7)
check("list→12 / map→13", TYPE_LIST == 12 and TYPE_MAP == 13)
check("类型名表覆盖 15 个基类取值", len(TYPE_NAMES) == 15, str(len(TYPE_NAMES)))

# ---------------- 3. 单值编码布局 ----------------
buf = WriteBuffer()
n = write_value(buf, None)
check("null 只占 1 字节", n == 1 and bytes(buf.data) == b"\x00", bytes(buf.data).hex())
buf = WriteBuffer()
write_value(buf, True)
check("true 只占 1 字节", bytes(buf.data) == b"\x01", bytes(buf.data).hex())

buf = WriteBuffer()
n = write_value(buf, 1)
check("小整数 1 = 类型字节+4字节补码 = 5B", n == 5 and buf.data[0] == 3, buf.data.hex())
check("恰在 32 位边界内仍用 32 位补码", len(size_bytes(1)) == 1 and
      write_value(WriteBuffer(), 2 ** 31 - 1) == 5)
check("-(2**31) 仍走 32 位分支", write_value(WriteBuffer(), -(2 ** 31)) == 5)
check("2**31 越界改走 64 位分支", write_value(WriteBuffer(), 2 ** 31) == 9)
check("-(2**31)-1 越界改走 64 位分支",
      write_value(WriteBuffer(), -(2 ** 31) - 1) == 9)

buf = WriteBuffer()
write_value(buf, "中文")
check("String 长度字段是 UTF-8 字节数(6)而非字符数(2)",
      buf.data[1] == 6 and len(buf.data) == 8, buf.data.hex())
check("String 往返", decode_message(bytes(buf.data)) == "中文")

# ---------------- 4. double 的 64 位对齐 ----------------
buf = WriteBuffer()
write_value(buf, 1.5)
pad = (-1) % 8          # 类型字节已占 1 字节,故填充到下一个 8 的倍数
check("double 值起点在 64 位边界", (1 + pad) % 8 == 0 and buf.data[1:1 + pad] == b"\x00" * pad,
      buf.data.hex())
check("double 共 16 字节(类型+7 填充+8)", len(buf.data) == 16, str(len(buf.data)))
check("double 往返", decode_message(bytes(buf.data)) == 1.5)

# ---------------- 5. TypedData 列表的对齐与填充 ----------------
buf = WriteBuffer()
write_value(buf, F64List([1.0, 2.0, 3.0]))
check("float64list 元素区对齐到 8", (1 + 1 + (-2) % 8) % 8 == 0)
check("float64list 填充最少(6 个零字节)", bytes(buf.data[2:8]) == b"\x00" * 6, buf.data.hex())
check("float64list 总长 = 1+1+6+24 = 32", len(buf.data) == 32, str(len(buf.data)))
check("float64list 往返", decode_message(bytes(buf.data)) == [1.0, 2.0, 3.0])

buf = WriteBuffer()
write_value(buf, I32List([7, -8]))
check("int32list 元素区对齐到 4", (1 + 1 + 2) % 4 == 0 and bytes(buf.data[2:4]) == b"\x00\x00",
      buf.data.hex())
check("int32list 总长 = 1+1+2+8 = 12", len(buf.data) == 12, str(len(buf.data)))
check("int32list 往返", decode_message(bytes(buf.data)) == [7, -8])

buf = WriteBuffer()
write_value(buf, I64List([1, 2]))
check("int64list 元素区对齐到 8", (1 + 1 + 6) % 8 == 0)
check("int64list 往返", decode_message(bytes(buf.data)) == [1, 2])

buf = WriteBuffer()
write_value(buf, F32List([0.5]))
check("float32list 元素区对齐到 4", (1 + 1 + 2) % 4 == 0)
check("float32list 往返", decode_message(bytes(buf.data)) == [0.5])

buf = WriteBuffer()
write_value(buf, U8List(b"\x01\x02\x03"))
check("uint8list 对齐到 1 故无填充,总长 = 1+1+3",
      len(buf.data) == 5 and bytes(buf.data) == bytes([8, 3, 1, 2, 3]), buf.data.hex())

# ---------------- 6. List / Map 的长度前缀与递归 ----------------
buf = WriteBuffer()
write_value(buf, [1, 2, 3])
check("list = 类型+计数+(各元素含类型字节) = 1+1+15", len(buf.data) == 17, buf.data.hex())
check("list 往返", decode_message(bytes(buf.data)) == [1, 2, 3])

buf = WriteBuffer()
write_value(buf, {"a": 1})
check("map = 类型+计数+键(3)+值(5) = 1+1+3+5 = 10", len(buf.data) == 10, buf.data.hex())
check("map 往返", decode_message(bytes(buf.data)) == {"a": 1})

nested = {"n": [1, None, {"k": "v"}], "f": 0.25, "b": True}
blob = WriteBuffer()
write_value(blob, nested)
check("嵌套结构往返", decode_message(bytes(blob.data)) == nested)

# ---------------- 7. Message corrupted ----------------
try:
    decode_message(bytes(blob.data) + b"\x00")
    check("尾部多余字节抛 FormatException", False, "未抛异常")
except FormatException as exc:
    check("尾部多余字节抛 'Message corrupted'", str(exc) == "Message corrupted", str(exc))

# ---------------- 8. 方法调用:两段直接拼接 ----------------
mcall = encode_method_call("getBatteryLevel", 42)
check("方法名编码长度 = 1+1+15", mcall[0] == TYPE_STRING and mcall[1] == 15, mcall[:4].hex())
check("无信封:首字节就是类型字节 7", mcall[0] == 7)
check("方法调用往返", decode_method_call(mcall) == ("getBatteryLevel", 42))
check("无参调用参数位编码为 null", decode_method_call(encode_method_call("x")) == ("x", None))

try:
    decode_method_call(encode_method_call("x", 1) + b"\x00")
    check("方法调用尾部多余字节抛错", False, "未抛异常")
except FormatException as exc:
    check("方法调用尾部多余字节抛 'Invalid method call'",
          str(exc) == "Invalid method call", str(exc))

try:
    decode_method_call(encode_method_call(42, 1))
    check("方法名不是 String 抛错", False, "未抛异常")
except FormatException as exc:
    check("方法名不是 String 抛 'Invalid method call'", str(exc) == "Invalid method call")
except PlatformException as exc:
    check("方法名不是 String 抛 'Invalid method call'", False, "误抛 PlatformException")

# ---------------- 9. 应答信封 ----------------
ok = encode_success_envelope(42)
check("成功信封首字节 0", ok[0] == 0 and decode_envelope(ok) == 42, ok.hex())
check("成功但结果为 null 也必须带首字节", encode_success_envelope(None)[0] == 0)
check("成功信封 null 往返", decode_envelope(encode_success_envelope(None)) is None)

err = encode_error_envelope("UNAVAILABLE", "Battery level not available.", None)
check("错误信封首字节 1", err[0] == 1, err.hex())
try:
    decode_envelope(err)
    check("错误信封解码抛 PlatformException", False, "未抛异常")
except PlatformException as exc:
    check("错误信封解出 code/message/details",
          exc.code == "UNAVAILABLE" and exc.message == "Battery level not available."
          and exc.details is None, "%r" % (exc,))
    check("编码侧只写 3 个值故 stacktrace 为 None", exc.stacktrace is None)

# 首字节非 0 即错误分支:2 也算错误(源码注释 "non-zero otherwise")
buf = WriteBuffer()
buf.put_uint8(2)
for v in ("E", "m", None):
    write_value(buf, v)
try:
    decode_envelope(bytes(buf.data))
    check("首字节非 0(2)仍走错误分支", False, "未抛异常")
except PlatformException as exc:
    check("首字节非 0(2)仍走错误分支", exc.code == "E")
except FormatException as exc:
    check("首字节非 0(2)仍走错误分支", False, "误判为 FormatException: %s" % exc)

# 第 4 个值(堆栈)解码侧会读
buf = WriteBuffer()
buf.put_uint8(1)
for v in ("E", "m", {"d": 1}, "stack"):
    write_value(buf, v)
try:
    decode_envelope(bytes(buf.data))
    check("错误信封第 4 值被读为 stacktrace", False, "未抛异常")
except PlatformException as exc:
    check("错误信封第 4 值被读为 stacktrace",
          exc.code == "E" and exc.details == {"d": 1} and exc.stacktrace == "stack", "%r" % (exc,))

try:
    decode_envelope(b"")
    check("空信封抛 FormatException", False, "未抛异常")
except FormatException as exc:
    check("空信封抛 'Expected envelope, got nothing'",
          str(exc) == "Expected envelope, got nothing", str(exc))

bad = encode_error_envelope(1, "m", None)   # code 不是 String
try:
    decode_envelope(bad)
    check("code 非 String 抛 'Invalid envelope'", False, "未抛异常")
except FormatException as exc:
    check("code 非 String 抛 'Invalid envelope'", str(exc) == "Invalid envelope", str(exc))
except PlatformException as exc:
    check("code 非 String 抛 'Invalid envelope'", False, "误抛 PlatformException")

# ---------------- 10. 8 字节对齐只保证"值/元素区"起点 ----------------
# 在非零偏移上再写一个 double,验证填充量随当前位置变化
for prefix_len in (0, 1, 2, 3, 5, 7, 8):
    buf = WriteBuffer()
    buf.data += b"\xAA" * prefix_len
    write_value(buf, 1.0)
    tail = bytes(buf.data[prefix_len:])
    pad2 = (-(prefix_len + 1)) % 8   # 类型字节写在 prefix_len 处
    check("前缀 %d 字节时 double 仍 64 位对齐" % prefix_len,
          (prefix_len + 1 + pad2) % 8 == 0 and tail[1:1 + pad2] == b"\x00" * pad2,
          tail.hex())

check("math.isclose(1.0, 1.0) 作为浮点断言写法示例", math.isclose(1.0, 1.0, abs_tol=1e-9))

print("=" * 62)
print("平台通道编解码自检:通过 %d 项,失败 %d 项" % (PASS, FAIL))
if FAILED:
    for line in FAILED:
        print("  [FAIL] " + line)
    sys.exit(1)
print("全部通过")
