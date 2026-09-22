"""RFC 6455 §5.5 控制帧 + §7.4 状态码治理。

口径全部来自实读原文，不掺推测：
  - §5.5：控制帧的操作码最高位为 1；已定义 0x8 Close / 0x9 Ping / 0xA Pong，
    0xB-0xF 保留；**所有控制帧的载荷长度必须 ≤ 125 字节且不得分片**。
  - §5.4：控制帧可以插在分片消息中间；控制帧自身不得分片；
    中间节点不得改动控制帧的分片。
  - §5.5.1：Close 帧若有 body，前 2 字节必须是网络字节序的无符号状态码。
  - §7.4.1：1005 / 1006 / 1015 是保留值，端点**不得**把它写进 Close 帧。
  - §7.4.2：0-999 不使用；1000-2999 归本协议与扩展；3000-3999 归库/框架/
    应用（向 IANA 注册）；4000-4999 私有用途、不可注册。
  - IANA Close Code 注册表：1012 Service Restart / 1013 Try Again Later /
    1014 Bad Gateway；1016-2999 Unassigned；3000 Unauthorized /
    3003 Forbidden / 3008 Timeout。
"""

import struct

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

CONTROL_BIT = 0x8
MAX_CONTROL_PAYLOAD = 125

#: §7.4.1 明确"MUST NOT be set as a status code in a Close control frame"。
NEVER_SEND = frozenset({1005, 1006, 1015})

#: IANA 注册表中已分配的码（RFC 6455 定义的部分 + 后续注册的 1012/1013/1014
#: 与 3000/3003/3008）。用于判断"已分配"还是"未分配"。
ASSIGNED = frozenset({
    1000, 1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010, 1011,
    1012, 1013, 1014, 1015, 3000, 3003, 3008,
})

MEANING = {
    1000: "Normal Closure", 1001: "Going Away", 1002: "Protocol error",
    1003: "Unsupported Data", 1004: "Reserved", 1005: "No Status Rcvd",
    1006: "Abnormal Closure", 1007: "Invalid frame payload data",
    1008: "Policy Violation", 1009: "Message Too Big", 1010: "Mandatory Ext.",
    1011: "Internal Error", 1012: "Service Restart", 1013: "Try Again Later",
    1014: "Bad Gateway", 1015: "TLS handshake",
    3000: "Unauthorized", 3003: "Forbidden", 3008: "Timeout",
}


def is_control(opcode):
    """§5.5：控制帧的操作码最高位为 1。"""
    return bool(opcode & CONTROL_BIT)


def is_reserved_data(opcode):
    """§5.6：0x3-0x7 保留给未来的非控制帧。"""
    return 0x3 <= opcode <= 0x7


def is_reserved_control(opcode):
    """§5.5：0xB-0xF 保留给未来的控制帧。"""
    return 0xB <= opcode <= 0xF


def validate_control_frame(opcode, fin, payload_len):
    """§5.5 + §5.4：控制帧自身必须合法。

    三条硬约束：操作码最高位为 1、载荷 ≤ 125 字节、不得分片（FIN 必须为 1）。
    """
    errors = []
    if not is_control(opcode):
        errors.append("操作码 0x%x 不是控制帧（最高位为 0）" % opcode)
    if is_reserved_control(opcode):
        errors.append("操作码 0x%x 属于保留的控制帧 0xB-0xF" % opcode)
    if payload_len > MAX_CONTROL_PAYLOAD:
        errors.append("控制帧载荷 %d 字节，超过 125 字节上限" % payload_len)
    if not fin:
        errors.append("控制帧不得分片（FIN 必须为 1）")
    return errors


def may_interject(opcode, fin, payload_len):
    """§5.4：控制帧可以插在分片消息中间，前提是它自身合法。"""
    return is_control(opcode) and not validate_control_frame(opcode, fin, payload_len)


def encode_close_body(code, reason=""):
    """§5.5.1：body 前 2 字节是网络字节序的状态码，其后可选 UTF-8 原因串。

    返回 (字节串, 错误列表)。code 为 None 表示不携带状态码（body 为空）。
    """
    errors = []
    if code is None:
        if reason:
            errors.append("不带状态码时不能携带原因串")
            return b"", errors
        return b"", errors
    errors.extend(status_code_issues(code))
    if errors:
        return b"", errors
    try:
        tail = reason.encode("utf-8")
    except UnicodeEncodeError:
        return b"", ["原因串不是合法的 UTF-8"]
    return struct.pack("!H", code) + tail, errors


def parse_close_body(body):
    """解析 Close 帧的 body。

    返回 (状态码或 None, 原因串, 错误列表)。
    - body 为空 → 没有状态码（应用层可用 1005 表示"对方没给码"）。
    - body 只有 1 字节 → 非法：状态码必须占满 2 字节。
    """
    if len(body) == 0:
        return None, "", []
    if len(body) == 1:
        return None, "", ["Close body 只有 1 字节，状态码必须占满 2 字节"]
    code = struct.unpack("!H", body[:2])[0]
    try:
        reason = body[2:].decode("utf-8")
    except UnicodeDecodeError:
        return code, "", ["原因串不是合法的 UTF-8"]
    return code, reason, []


def status_code_issues(code):
    """§7.4 状态码的合法性。注意区分三类"不能用"：

    1. 1005/1006/1015 —— 保留值，端点 MUST NOT 写入 Close 帧（但可以拿来在
       应用层*表示*某种状态）。这是最常被写错的一条。
    2. 1004 —— RFC 里就写着 "Reserved"，含义待定。
    3. 1016-2999 —— 区间归本协议/扩展，但当前 IANA 表里是 Unassigned。
    """
    errors = []
    if code < 1000:
        errors.append("状态码 %d 落在 0-999，该区间不使用" % code)
    elif code in NEVER_SEND:
        errors.append("状态码 %d 是保留值，端点不得把它写入 Close 帧" % code)
    elif code == 1004:
        errors.append("状态码 1004 已保留，具体含义待定")
    elif 1016 <= code <= 2999:
        errors.append("状态码 %d 落在 1000-2999（本协议/扩展保留），当前未分配" % code)
    elif 4000 <= code <= 4999:
        errors.append("状态码 %d 落在 4000-4999 私有区间，不可向 IANA 注册" % code)
    elif code >= 5000:
        errors.append("状态码 %d 超出本协议定义的 0-4999 范围" % code)
    return errors


def code_range(code):
    """§7.4.2 的四段区间归属。"""
    if 0 <= code <= 999:
        return "0-999 不使用"
    if 1000 <= code <= 2999:
        return "1000-2999 本协议/修订/扩展"
    if 3000 <= code <= 3999:
        return "3000-3999 库/框架/应用（向 IANA 注册）"
    if 4000 <= code <= 4999:
        return "4000-4999 私有用途（不可注册）"
    return "超出 0-4999"


def is_registerable(code):
    """§7.4.2：只有 3000-3999 是"注册"语义；4000-4999 明说 can't be registered。"""
    return 3000 <= code <= 3999


def meaning_of(code):
    return MEANING.get(code, "Unassigned" if 1016 <= code <= 2999 else "")
