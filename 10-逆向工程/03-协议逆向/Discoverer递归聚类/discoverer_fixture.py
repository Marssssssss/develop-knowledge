#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""discoverer.py 的测试夹具：两个报文家族，分别承载 length 语义与 offset 语义。"""

MAGIC = b"\xff\x00\x00\x00"


def _bin(cmd, body):
    """家族 A：magic(4B) + cmd(1B) + body_len(2B, 大端) + body。"""
    return MAGIC + bytes([cmd]) + len(body).to_bytes(2, "big") + body


# cmd=0x01 三条 + cmd=0x03 两条：cmd 是天然的 FD token（仅 2 个取值）
FAMILY_A = [_bin(0x01, b"HELLO"), _bin(0x01, b"BYE"), _bin(0x01, b"USERNAME"),
            _bin(0x03, b"GETTING"), _bin(0x03, b"DELETE")]
# body 只有 2 字节 < TEXT_MIN=3 → 退化成两个 binary token → token 模式与主流不同
FAMILY_A_ODD = [_bin(0x01, b"OK")]

# 家族 B：0xEE 魔数 + offset(2B, 大端, 指向第 3 个 text token) + 4 个 text token。
# 前两个 text token 各变长 3 字节、末一个变长 4 字节，使
#   offset 值差 = -6，报文长度差 = -10，任一单个 token 长度差 ∈ {-3, 0, -4}
# —— 三者互不相等，于是只有 offset 判据能命中，length 判据落空。
FAMILY_B = [b"\xee" + o.to_bytes(2, "big") + body for o, body in [
    (11, b"ABC DEF GHI JKL"), (17, b"ABCDEF DEFGHI GHI JKLMNOP")]]
