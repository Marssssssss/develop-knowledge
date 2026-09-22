"""609 gRPC 与 Protocol Buffers 的 API 建模 —— 演示入口。

从"字节"到"语义"走一遍：varint / tag / packed 的字节布局，
字段号治理，拼接即合并，最后是 gRPC 状态码的分工。
"""

from grpcstatus import (
    classify_auth_failure,
    classify_bad_range,
    classify_denial,
    code_id,
    code_name,
    is_app_only,
    retry_hint,
)
from proto import (
    FIELD_MAX,
    expand_reserved_numbers,
    map_entries,
    map_from_entries,
    merge_messages,
    reserved_statement,
    tag_size_bytes,
    to_message,
    validate_field_number,
)
from wire import (
    decode_records,
    encode_len_field,
    encode_packed,
    encode_sint_field,
    encode_string_field,
    encode_varint_field,
    group_pairs,
    split_tag,
    tag_byte,
    varint_encode,
    zigzag_encode,
)


def hexs(data):
    return data.hex(" ")


def main():
    print("== varint 与 tag（encoding 指南的官方例子）==")
    print("  Test1 a=150            →", hexs(encode_varint_field(1, 150)), "（应为 08 96 01）")
    print("  Test2 b=\"testing\"      →", hexs(encode_string_field(2, "testing")),
          "（应为 12 07 74 65 73 74 69 6e 67）")
    print("  Test3 c=Test1{a:150}   →",
          hexs(encode_len_field(3, encode_varint_field(1, 150))), "（应为 1a 03 08 96 01）")
    print("  Test4 d=\"hello\",e=[1,2,3] →",
          hexs(encode_string_field(4, "hello") + encode_packed(5, [1, 2, 3])),
          "（应为 22 05 68 65 6c 6c 6f 2a 03 01 02 03）")
    print("  tag(1,VARINT) =", hexs(tag_byte(1, 0)), " tag(2,LEN) =", hexs(tag_byte(2, 2)))
    print("  150 的 varint 字节数:", len(varint_encode(150)),
          " 1 的:", len(varint_encode(1)),
          " uint64 上限的:", len(varint_encode((1 << 64) - 1)))

    print()
    print("== 负数的两种编码 ==")
    print("  int32 -2（补码，占满 10 字节）→", hexs(encode_varint_field(1, -2)))
    print("  sint32 -500 → zigzag =", zigzag_encode(-500, 32),
          " 与 999 同码 →", hexs(varint_encode(zigzag_encode(-500, 32)))
          == hexs(varint_encode(999)))
    for value in (0, -1, 1, -2):
        print("    zigzag(%3d) = %d" % (value, zigzag_encode(value, 32)))

    print()
    print("== 记录级解析（无 schema）==")
    blob = encode_string_field(4, "hello") + encode_packed(5, [1, 2, 3])
    for field_number, wire_type, value in decode_records(blob):
        print("    field=%d wire=%s value=%r"
              % (field_number, ["VARINT", "I64", "LEN", "SGROUP", "EGROUP", "I32"][wire_type],
                 value))

    print()
    print("== group 的字段号配对 ==")
    from wire import encode_group
    ok_group = encode_group(8, encode_varint_field(1, 2))
    bad_group = tag_byte(7, 3) + encode_varint_field(1, 2) + tag_byte(8, 4)
    print("  配对正确:", group_pairs(decode_records(ok_group)) or "OK")
    print("  7:SGROUP 配 8:EGROUP:", group_pairs(decode_records(bad_group)))

    print()
    print("== 字段号治理（proto3 指南）==")
    for number in (1, 15, 16, 19000, 19999, 536870911, 536870912, 0):
        print("    %-10d %s" % (number, validate_field_number(number) or "OK"))
    print("  tag 字节数: 1→%d, 15→%d, 16→%d, 2047→%d, 2048→%d"
          % (tag_size_bytes(1), tag_size_bytes(15), tag_size_bytes(16),
             tag_size_bytes(2047), tag_size_bytes(2048)))
    print("  reserved 2,15,9 to 11 →", sorted(expand_reserved_numbers([2, 15, (9, 11)])))
    print("  reserved 混写:", reserved_statement([2, "foo"])[2])

    print()
    print("== 拼接即合并（Last One Wins）==")
    a = {1: "first", 2: [1, 2], 3: {"x": 1, "y": [9]}}
    b = {1: "second", 2: [3], 3: {"y": [8], "z": 7}}
    print("  merge:", merge_messages(a, b))
    print("  标量取后者 / repeated 拼接 / 子消息递归合并")
    print("  to_message:", to_message([(1, "a"), (1, "b"), (2, 3)]))

    print()
    print("== map 即 repeated Entry ==")
    entries = map_entries({"a": 1, "b": 2})
    print("  entries:", entries)
    print("  还原:", map_from_entries(entries))

    print()
    print("== gRPC 状态码分工 ==")
    print("  NOT_FOUND id =", code_id("NOT_FOUND"), " id 16 =", code_name(16))
    print("  只由用户代码产生:", sorted(n for n in
                              ["INVALID_ARGUMENT", "NOT_FOUND", "ALREADY_EXISTS",
                               "FAILED_PRECONDITION", "ABORTED", "OUT_OF_RANGE",
                               "DATA_LOSS", "UNAVAILABLE", "INTERNAL"] if is_app_only(n)))
    print("  重试准则:", {n: retry_hint(n) for n in
                  ("UNAVAILABLE", "ABORTED", "FAILED_PRECONDITION", "INTERNAL")})
    print("  整类用户不可见 →", classify_denial("whole-class"))
    print("  同类部分用户被拒 →", classify_denial("within-class"))
    print("  配额耗尽 →", classify_auth_failure("quota-exhausted"))
    print("  无法识别调用方 →", classify_auth_failure("cannot-identify-caller"))
    print("  超出可表示范围 →", classify_bad_range("outside-representable"))
    print("  超过当前长度 →", classify_bad_range("beyond-current-size"))


if __name__ == "__main__":
    main()
