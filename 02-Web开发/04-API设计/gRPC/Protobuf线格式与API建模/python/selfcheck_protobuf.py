"""609 自检：Protobuf 线格式 + 字段号治理 + gRPC 状态码。

字节级断言全部对着 encoding 指南里给出的官方十六进制串；
语义级断言对着 proto3 指南与 gRPC status-codes 文档。
"""

import sys

from harness import check, expect_errors

from wire import (
    MAX_UINT64,
    concat_packed,
    decode_records,
    encode_double_field,
    encode_fixed32_field,
    encode_fixed64_field,
    encode_float_field,
    encode_group,
    encode_len_field,
    encode_packed,
    encode_sint_field,
    encode_string_field,
    encode_varint_field,
    group_pairs,
    split_tag,
    tag_byte,
    to_signed64,
    varint_decode,
    varint_encode,
    varint_size,
    zigzag_decode,
    zigzag_encode,
)
from proto import (
    FIELD_MAX,
    RESERVED_IMPL_HIGH,
    RESERVED_IMPL_LOW,
    expand_reserved_numbers,
    map_entries,
    map_from_entries,
    merge_messages,
    reserved_statement,
    tag_size_bytes,
    to_message,
    validate_field_number,
)
from grpcstatus import (
    APP_ONLY,
    CODES,
    classify_auth_failure,
    classify_bad_range,
    classify_denial,
    code_id,
    code_name,
    is_app_only,
    is_success,
    library_generated,
    retry_hint,
)

# ---------------------------------------------------------------- varint

check(varint_encode(1) == b"\x01", "1 编码为单字节")
check(varint_encode(127) == b"\x7f", "127 仍是一字节")
check(varint_encode(128) == b"\x80\x01", "128 起需要两字节（小端、MSB 续接）")
check(varint_encode(150) == b"\x96\x01", "官方例子：150 → 96 01")
check(varint_encode(300) == b"\xac\x02", "300 → ac 02")
check(varint_encode(0) == b"\x00", "0 编码为单字节 00")
check(varint_size(MAX_UINT64) == 10, "uint64 上限恰好 10 字节")
check(varint_size(1 << 63) == 10, "最高位为 1 时也是 10 字节")
check(varint_size(1 << 56) == 9, "2^56 需要 9 字节")
check(varint_decode(b"\x96\x01") == (150, 2), "varint 解码返回值与新位置")
check(varint_decode(b"\x96\x01extra", 0)[0] == 150, "多余字节不参与解析")
check(varint_decode(b"\x01", 0) == (1, 1), "单字节 varint 的位移")

# ------------------------------------------------------------ ZigZag

check([zigzag_encode(v, 32) for v in (0, -1, 1, -2)] == [0, 1, 2, 3],
      "官方 ZigZag 表：0→0、-1→1、1→2、-2→3")
check(zigzag_encode(0x7FFFFFFF, 32) == 0xFFFFFFFE, "0x7fffffff → 0xfffffffe")
check(zigzag_encode(-0x80000000, 32) == 0xFFFFFFFF, "-0x80000000 → 0xffffffff")
check(zigzag_encode(-500, 32) == 999, "官方例子：-500z == varint 999")
check(varint_encode(zigzag_encode(-500, 32)) == varint_encode(999),
      "-500 的 sint32 编码与 999 的 varint 编码逐字节相同")
check(zigzag_decode(999) == -500, "ZigZag 解码还原 -500")
check(all(zigzag_decode(zigzag_encode(v, 64)) == v for v in (0, 1, -1, 500, -500, 1 << 40)),
      "ZigZag 往返一致")
check(varint_size(zigzag_encode(-1, 64)) == 1, "sint64 -1 只占 1 字节（补码要 10 字节）")
check(varint_size((-2) & MAX_UINT64) == 10, "int64 -2 的补码占满 10 字节")

# ---------------------------------------------------------------- tag

check((1 << 3) | 0 == 0x08, "Test1 的 tag")
check(tag_byte(1, 0) == b"\x08", "tag(1,VARINT) 的字节")
check(tag_byte(2, 2) == b"\x12", "tag(2,LEN) 的字节")
check(tag_byte(3, 2) == b"\x1a", "tag(3,LEN) 的字节")
check(tag_byte(5, 2) == b"\x2a", "tag(5,LEN) 的字节")
check(split_tag(0x08) == (1, 0), "低 3 位是线类型，其余是字段号")
check(split_tag(0x12) == (2, 2), "0x12 → 字段 2、线类型 LEN")
check(split_tag(0x1f) == (3, 7), "线类型 7 是保留值，本 demo 不臆造语义")

# -------------------------------------------- 官方四个示例的逐字节复现

check(encode_varint_field(1, 150) == bytes.fromhex("089601"),
      "Test1 a=150 → 08 96 01")
check(encode_string_field(2, "testing") == bytes.fromhex("120774657374696e67"),
      "Test2 b=\"testing\" → 12 07 74 65 73 74 69 6e 67")
check(encode_len_field(3, encode_varint_field(1, 150)) == bytes.fromhex("1a03089601"),
      "Test3 c=Test1{a:150} → 1a 03 08 96 01")
check(encode_string_field(4, "hello") + encode_packed(5, [1, 2, 3])
      == bytes.fromhex("22056865" "6c6c6f2a03010203"),
      "Test4 d=\"hello\", e=[1,2,3] → 22 05 ... 2a 03 01 02 03")

# -------------------------------------------------------- 负数与定长

check(encode_varint_field(1, -2) == bytes.fromhex("08" + "fe" + "ff" * 8 + "01"),
      "int32 -2 的补码占满 10 字节（fe ff ff ff ff ff ff ff ff 01）")
check(to_signed64(varint_decode(bytes.fromhex("fe" * 1 + "ff" * 8 + "01"))[0]) == -2,
      "补码解码回 -2")
check(encode_fixed32_field(1, 0x01020304) == bytes.fromhex("0d" + "04030201"),
      "I32 是小端四字节")
check(encode_fixed64_field(1, 0x0102030405060708)
      == bytes.fromhex("09" + "0807060504030201"), "I64 是小端八字节")
check(encode_float_field(2, 1.5) == bytes.fromhex("150000c03f"), "float 是 IEEE 754 单精度")
check(encode_double_field(2, 1.5) == bytes.fromhex("11000000000000f83f"),
      "double 是 IEEE 754 双精度")

# -------------------------------------------------------------- packed

PACKED = encode_packed(5, [1, 2, 3])
check(PACKED == bytes.fromhex("2a030102 03".replace(" ", "")), "packed 只发一个 LEN 记录")
check(decode_records(PACKED)[0][1] == 2, "packed 记录的线类型是 LEN")
check(decode_records(PACKED)[0][2] == b"\x01\x02\x03", "packed 载荷是各元素的拼接")
check(concat_packed([b"\x01\x02", b"\x03"]) == [1, 2, 3],
      "解析器 MUST 接受多个 packed 键值对并拼接")
check(concat_packed([b"", b"\x01"]) == [1], "空载荷不干扰拼接")
check(encode_packed(5, []) == bytes.fromhex("2a00"), "空 repeated 也发一个长度 0 的 LEN")
check(decode_records(encode_packed(5, [300]))[0][2] == varint_encode(300),
      "packed 元素各自按 varint 编码（可多字节）")

# --------------------------------------------------------------- group

check(group_pairs(decode_records(encode_group(8, encode_varint_field(1, 2)))) == [],
      "字段号配对的 group 合法")
BAD_GROUP = tag_byte(7, 3) + encode_varint_field(1, 2) + tag_byte(8, 4)
expect_errors([m for _i, m in group_pairs(decode_records(BAD_GROUP))],
              ["EGROUP 字段号 8 与 SGROUP 7 不符"], label="group 字段号必须配对")
DANGLING = tag_byte(8, 3)
expect_errors([m for _i, m in group_pairs(decode_records(DANGLING))],
              ["未闭合"], label="未闭合的 group")

# ---------------------------------------------------------- 记录级解析

BLOB = encode_string_field(4, "hello") + encode_packed(5, [1, 2, 3])
RECORDS = decode_records(BLOB)
check([r[0] for r in RECORDS] == [4, 5], "记录顺序即写入顺序")
check(RECORDS[0][2] == b"hello", "LEN 记录取回原始字节")
check(decode_records(b"") == [], "空消息解析为空记录列表")
check(decode_records(encode_varint_field(1, 0))[0] == (1, 0, 0),
      "显式写 0 也会被写出（隐式字段才省略）")

# ------------------------------------------------------- 字段号治理

check(validate_field_number(1) == [], "字段号下界")
check(validate_field_number(FIELD_MAX) == [], "字段号上界 536,870,911")
expect_errors(validate_field_number(0), ["小于下限"], label="字段号不能为 0")
expect_errors(validate_field_number(FIELD_MAX + 1), ["大于上限"], label="超过 29 位上限")
expect_errors(validate_field_number(RESERVED_IMPL_LOW), ["实现保留区间"],
              label="19000 保留")
expect_errors(validate_field_number(RESERVED_IMPL_HIGH), ["实现保留区间"],
              label="19999 保留")
check(validate_field_number(RESERVED_IMPL_LOW - 1) == [], "18999 可用")
check(validate_field_number(RESERVED_IMPL_HIGH + 1) == [], "20000 可用")
expect_errors(validate_field_number(True), ["必须是整数"], label="bool 不是合法字段号")

check(tag_size_bytes(1) == 1, "字段 1 的 tag 占 1 字节")
check(tag_size_bytes(15) == 1, "字段 15 的 tag 占 1 字节")
check(tag_size_bytes(16) == 2, "字段 16 的 tag 占 2 字节")
check(tag_size_bytes(2047) == 2, "字段 2047 的 tag 占 2 字节")
check(tag_size_bytes(2048) == 3, "字段 2048 的 tag 占 3 字节")

check(expand_reserved_numbers([2, 15, (9, 11)]) == {2, 9, 10, 11, 15},
      "reserved 区间是闭区间（9 to 11 = 9,10,11）")
check(expand_reserved_numbers([(5, 5)]) == {5}, "单元素区间")
NUMS, NAMES, RESERVED_ERRORS = reserved_statement([2, "foo"])
expect_errors(RESERVED_ERRORS, ["不能写在同一条 reserved 语句里"],
              label="字段号与字段名不得混写")
check(NUMS == {2} and NAMES == {"foo"}, "混写时两边仍各自解析出来")
check(reserved_statement([2, (9, 11)])[2] == [], "纯数字合法")
check(reserved_statement(["foo", "bar"])[2] == [], "纯名字合法")
expect_errors(reserved_statement([(11, 9)])[2], ["下界大于上界"], label="倒置区间")
expect_errors(reserved_statement([True])[2], ["布尔不是合法字段号"], label="布尔字段号")

# ------------------------------------------------------- 拼接即合并

A = {1: "first", 2: [1, 2], 3: {"x": 1, "y": [9]}}
B = {1: "second", 2: [3], 3: {"y": [8], "z": 7}}
MERGED = merge_messages(A, B)
check(MERGED[1] == "second", "标量/字符串：last one wins")
check(MERGED[2] == [1, 2, 3], "repeated：拼接")
check(MERGED[3] == {"x": 1, "y": [9, 8], "z": 7}, "子消息：递归合并（标量子字段覆盖）")
check(merge_messages({}, B) == B, "空消息合并等于对方")
check(merge_messages(A, {}) == A, "与空消息合并不变")
check(merge_messages({1: [1]}, {1: [2]})[1] == [1, 2], "两边都是 repeated 时仍拼接")
check(merge_messages({1: {"a": [1]}}, {1: {"a": [2]}})[1] == {"a": [1, 2]},
      "子消息里的 repeated 同样是拼接而非覆盖")
check(to_message([(1, "a"), (1, "b"), (2, 3)]) == {1: ["a", "b"], 2: 3},
      "同一字段号出现多次即 repeated")
check(to_message([(1, "a")]) == {1: "a"}, "单次出现是单值")

# --------------------------------------------------------------- map

ENTRIES = map_entries({"a": 1, "b": 2})
check(ENTRIES == [{1: "a", 2: 1}, {1: "b", 2: 2}], "map 即 repeated Entry{key=1,value=2}")
check(map_from_entries(ENTRIES) == {"a": 1, "b": 2}, "Entry 列表还原成 dict")
check(map_from_entries([{1: "a", 2: 1}, {1: "a", 2: 9}]) == {"a": 9},
      "重复 key 按 last one wins")

# --------------------------------------------------------- gRPC 状态码

check(len(CODES) == 17, "官方共 17 个状态码（0..16）")
check([c[1] for c in CODES] == list(range(17)), "id 连续 0..16")
check(code_id("OK") == 0 and code_name(0) == "OK", "OK 是 0")
check(code_id("UNAUTHENTICATED") == 16, "UNAUTHENTICATED 是 16")
check(code_name(14) == "UNAVAILABLE", "14 是 UNAVAILABLE")
check(code_id("NOT_A_CODE") is None, "未知码名返回 None")
check(code_name(99) is None, "未知 id 返回 None")
check(is_success("OK") and not is_success("UNKNOWN"), "只有 OK 是成功")

check(sorted(APP_ONLY) == sorted([
    "INVALID_ARGUMENT", "NOT_FOUND", "ALREADY_EXISTS", "FAILED_PRECONDITION",
    "ABORTED", "OUT_OF_RANGE", "DATA_LOSS"]),
    "库从不生成的 7 个码")
check(len(APP_ONLY) == 7, "恰好 7 个（UNAVAILABLE / INTERNAL 等仍可能由库产生）")
check(is_app_only("NOT_FOUND"), "NOT_FOUND 只来自用户代码")
check(not is_app_only("UNAVAILABLE"), "UNAVAILABLE 可能由库产生")
check(library_generated("DEADLINE_EXCEEDED"), "DEADLINE_EXCEEDED 可由库产生")
check(not library_generated("ABORTED"), "ABORTED 不由库产生")

check(retry_hint("UNAVAILABLE") == "retry-call", "(a) 只重试失败的那次调用")
check(retry_hint("ABORTED") == "retry-higher-level", "(b) 在更高层重试")
check(retry_hint("FAILED_PRECONDITION") == "no-retry", "(c) 状态修好前不重试")
check(retry_hint("INTERNAL") is None, "规范未给 INTERNAL 的重试准则")

check(classify_denial("whole-class") == "NOT_FOUND", "整类用户不可见 → NOT_FOUND")
check(classify_denial("within-class") == "PERMISSION_DENIED",
      "同类中的部分用户被拒 → PERMISSION_DENIED")
check(classify_auth_failure("quota-exhausted") == "RESOURCE_EXHAUSTED",
      "资源耗尽不得用 PERMISSION_DENIED")
check(classify_auth_failure("cannot-identify-caller") == "UNAUTHENTICATED",
      "无法识别调用方 → UNAUTHENTICATED")
check(classify_auth_failure("no-permission") == "PERMISSION_DENIED", "无权限")
check(classify_bad_range("outside-representable") == "INVALID_ARGUMENT",
      "32 位文件系统读 [0,2^32-1] 之外的偏移 → INVALID_ARGUMENT")
check(classify_bad_range("beyond-current-size") == "OUT_OF_RANGE",
      "超过当前文件长度 → OUT_OF_RANGE（遍历场景用它判定结束）")
check(classify_denial("other") is None and classify_auth_failure("x") is None,
      "未列出的场景不臆造映射")

if __name__ == "__main__":
    print()
    import harness
    if harness.FAILURES:
        print("FAILED %d: %s" % (len(harness.FAILURES), harness.FAILURES[:5]))
        sys.exit(1)
    print("ALL PASS (%d 断言)" % harness.count())
