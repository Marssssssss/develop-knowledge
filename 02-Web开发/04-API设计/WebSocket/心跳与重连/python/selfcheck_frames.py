"""610 自检（一）：控制帧 / Close 帧编解码 / 状态码治理。

对应 frames.py，口径见该模块头部。断言成对构造：合法样例必须零错误，
非法样例必须命中**预期那一条**且不得命中无关条款（防误报）。
"""

from harness import check, expect_errors
from frames import (OP_CONTINUATION, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG,
                    MAX_CONTROL_PAYLOAD, NEVER_SEND, is_control, is_reserved_data,
                    is_reserved_control, validate_control_frame, may_interject,
                    encode_close_body, parse_close_body, status_code_issues,
                    code_range, is_registerable, meaning_of)


def section_frames():
    check(MAX_CONTROL_PAYLOAD == 125, "控制帧载荷上限 125")
    for op in (OP_CLOSE, OP_PING, OP_PONG):
        check(is_control(op), "0x%x 是控制帧" % op)
    for op in (OP_CONTINUATION, OP_TEXT, OP_BINARY, 0x7):
        check(not is_control(op), "0x%x 不是控制帧" % op)
    for op in (0x3, 0x4, 0x5, 0x6, 0x7):
        check(is_reserved_data(op), "0x%x 属于保留数据帧" % op)
    check(not is_reserved_data(0x2) and not is_reserved_data(0x8),
          "0x2/0x8 不是保留数据帧")
    for op in (0xB, 0xC, 0xD, 0xE, 0xF):
        check(is_reserved_control(op), "0x%x 属于保留控制帧" % op)
    check(not is_reserved_control(OP_PONG) and not is_reserved_control(0x10),
          "0xA/0x10 不是保留控制帧")

    for op in (OP_CLOSE, OP_PING, OP_PONG):
        expect_errors(validate_control_frame(op, True, 0), (),
                      ("载荷", "分片", "不是控制帧", "保留"), "合法控制帧 %s" % op)
    expect_errors(validate_control_frame(OP_PING, True, 125), (),
                  ("超过",), "125 字节合法（闭区间上界）")
    expect_errors(validate_control_frame(OP_PING, True, 126), ("超过 125 字节上限",),
                  ("分片", "不是控制帧"), "126 字节非法")
    expect_errors(validate_control_frame(OP_PING, False, 4), ("不得分片",),
                  ("载荷", "不是控制帧"), "控制帧不得分片")
    expect_errors(validate_control_frame(OP_TEXT, True, 4), ("不是控制帧",),
                  ("载荷", "分片"), "数据帧不能当控制帧")
    expect_errors(validate_control_frame(0xB, True, 4), ("保留的控制帧",),
                  ("载荷", "分片"), "0xB 是保留控制帧")
    # 同一帧既超长又被分片：两条都要报，不能短路只报一条
    expect_errors(validate_control_frame(OP_CLOSE, False, 200),
                  ("超过 125 字节上限", "不得分片"), (), "超长+分片两条都报")

    check(may_interject(OP_PING, True, 4), "控制帧可插在分片消息中间")
    check(not may_interject(OP_TEXT, True, 4), "数据帧不存在'插在中间'的问题")
    check(not may_interject(OP_PING, False, 4), "被分片的控制帧本身已非法")


def section_close_body():
    body, errs = encode_close_body(1000, "bye")
    check(not errs and body == b"\x03\xe8bye", "Close body 状态码网络字节序")
    check(body[:2] == (1000).to_bytes(2, "big"), "前 2 字节 = 状态码大端")
    body, errs = encode_close_body(None)
    check(not errs and body == b"", "无状态码则 body 为空")
    body, errs = encode_close_body(None, "x")
    expect_errors(errs, ("不带状态码时不能携带原因串",), (), "无码不能带原因串")

    check(parse_close_body(b"") == (None, "", []), "空 body → 无状态码")
    code, reason, errs = parse_close_body(b"\x03")
    expect_errors(errs, ("只有 1 字节",), (), "1 字节 body 非法")
    check(code is None and reason == "", "1 字节 body 不产出状态码")

    body, _ = encode_close_body(1001, "重启")
    code, reason, errs = parse_close_body(body)
    check(not errs and code == 1001 and reason == "重启", "中文原因串往返一致")

    bad_tail = (1000).to_bytes(2, "big") + b"\xff\xfe"
    _, _, errs = parse_close_body(bad_tail)
    expect_errors(errs, ("UTF-8",), (), "非法 UTF-8 原因串要报错")

    for reserved in sorted(NEVER_SEND):
        body, errs = encode_close_body(reserved)
        expect_errors(errs, ("不得把它写入 Close 帧",), (),
                      "保留码 %d 不得写入" % reserved)
        check(body == b"", "保留码 %d 不产出字节" % reserved)
    check(NEVER_SEND == frozenset({1005, 1006, 1015}), "三个 MUST-NOT 保留码")

    body, errs = encode_close_body(3000)
    check(not errs and body == (3000).to_bytes(2, "big"), "3000 可写入（IANA 已注册）")


def section_status_codes():
    check(code_range(0) == "0-999 不使用" and code_range(999) == "0-999 不使用",
          "0-999 区间")
    check(code_range(1000) == "1000-2999 本协议/修订/扩展", "1000 下界")
    check(code_range(2999) == "1000-2999 本协议/修订/扩展", "2999 上界")
    check(code_range(3000) == "3000-3999 库/框架/应用（向 IANA 注册）", "3000 下界")
    check(code_range(3999) == "3000-3999 库/框架/应用（向 IANA 注册）", "3999 上界")
    check(code_range(4000) == "4000-4999 私有用途（不可注册）", "4000 下界")
    check(code_range(4999) == "4000-4999 私有用途（不可注册）", "4999 上界")
    check(code_range(5000) == "超出 0-4999", "5000 已越界")

    for code in (1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011,
                 1012, 1013, 1014, 3000):
        expect_errors(status_code_issues(code), (), ("保留值", "未分配", "不注册",
                                                     "不使用", "超出"),
                      "状态码 %d 可发出" % code)
    for code in sorted(NEVER_SEND):
        expect_errors(status_code_issues(code), ("保留值",), ("未分配",),
                      "状态码 %d 禁发" % code)
    expect_errors(status_code_issues(1004), ("1004 已保留",), ("保留值，端点不得",),
                  "1004 保留但表述不同")
    expect_errors(status_code_issues(2000), ("未分配",), ("保留值",),
                  "1016-2999 未分配")
    expect_errors(status_code_issues(999), ("0-999",), ("保留值",), "0-999 不使用")
    expect_errors(status_code_issues(4500), ("私有区间",), ("未分配",),
                  "4000-4999 私有")
    expect_errors(status_code_issues(6000), ("超出",), ("私有区间",), "≥5000 越界")

    # is_registerable 只有 3000-3999 为真，私有区间明说 can't be registered
    for code in (3000, 3999):
        check(is_registerable(code), "%d 可注册" % code)
    for code in (2999, 4000, 4999, 5000):
        check(not is_registerable(code), "%d 不可注册" % code)

    check(meaning_of(1012) == "Service Restart", "1012 Service Restart")
    check(meaning_of(1013) == "Try Again Later", "1013 Try Again Later")
    check(meaning_of(1014) == "Bad Gateway", "1014 Bad Gateway")
    check(meaning_of(3008) == "Timeout", "3008 Timeout")
    check(meaning_of(2000) == "Unassigned", "2000 未分配")
    check(meaning_of(1000) == "Normal Closure", "1000 Normal Closure")


def run():
    section_frames()
    section_close_body()
    section_status_codes()
