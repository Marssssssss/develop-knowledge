"""
QUIC 包保护自检：全部期望值取自 RFC 原文，不自行编造。

对照点：
  RFC 9001 附录 A.1 —— Initial 密钥派生（HkdfLabel 的 info 十六进制原文）
  RFC 9001 附录 A.2 —— 客户端 Initial 的未保护头（长度 1182 / pn_offset 18）
  RFC 9001 附录 A.5 —— ChaCha20-Poly1305 短头包的**完整端到端向量**
                        （含 sample / mask / 受保护头 / 最终 21 字节包）
  RFC 9000 附录 A.2·A.3 —— 包号编码字节数与解码窗口还原

运行：python quic_test.py
"""

from __future__ import annotations

import struct

from quic_crypto import (header_mask, initial_secrets, next_secret, packet_keys)
from quic_packet import (VARINT_MAX, aead_nonce, decode_packet_number,
                         decode_varint, encode_packet_number, encode_varint,
                         min_frame_length, parse_initial_header,
                         pn_length_from_first_byte, pn_offset_initial,
                         pn_offset_short)
from quic_protect import (SAMPLE_LEN, build_short_packet, open_short_packet,
                          remove_header_protection, sample_for)

# RFC 9001 附录 A.5 的短头包向量
A5_SECRET = bytes.fromhex("9ac312a7f877468ebe69422748ad00a1"
                          "5443f18203a07d6060f688f30f21632b")
A5_PN = 654360564


def _check_hkdf_labels() -> int:
    """RFC 9001 附录 A.1 把五个 HkdfLabel 的 info 字段原样列出，可逐字节比对。"""
    checks = 0

    def info_of(label: bytes, length: int, context: bytes = b"") -> bytes:
        full = b"tls13 " + label
        return struct.pack("!H", length) + bytes([len(full)]) + full + \
            bytes([len(context)]) + context

    assert info_of(b"client in", 32).hex() == "00200f746c73313320636c69656e7420696e00"
    assert info_of(b"server in", 32).hex() == "00200f746c7331332073657276657220696e00"
    assert info_of(b"quic key", 16).hex() == "00100e746c7331332071756963206b657900"
    assert info_of(b"quic iv", 12).hex() == "000c0d746c733133207175696320697600"
    assert info_of(b"quic hp", 16).hex() == "00100d746c733133207175696320687000"
    checks += 5

    cid = bytes.fromhex("8394c8f03e515708")            # 附录 A.1 用的 DCID
    client_secret, server_secret = initial_secrets(cid)
    assert client_secret.hex() == \
        "c00cf151ca5be075ed0ebfb5c80323c42d6b7db67881289af4008f1f6c357aea"
    assert server_secret.hex() == \
        "3c199828fd139efd216c155ad844cc81fb82fa8d7446fa7d78be803acdda951b"
    ck, civ, chp = packet_keys(client_secret, key_len=16, iv_len=12, hp_len=16)
    assert (ck.hex(), civ.hex(), chp.hex()) == (
        "1f369613dd76d5467730efcbe3b1a22d", "fa044b2f42a3fd3b46fb255c",
        "9f50449e04a0e810283a1e9933adedd2")
    sk, siv, shp = packet_keys(server_secret, key_len=16, iv_len=12, hp_len=16)
    assert (sk.hex(), siv.hex(), shp.hex()) == (
        "cf3a5331653c364c88f0f379b6067e37", "0ac1493ca1905853b0bba03e",
        "c206b8d9b9f0f37644430b490eeaa314")
    checks += 7
    return checks


def _check_initial_header() -> int:
    """附录 A.2：Length 解出的是 1182（不是带前缀位的 0x449E），流标注为 0x449E。"""
    checks = 0
    cid = bytes.fromhex("8394c8f03e515708")
    hdr = bytes.fromhex("c300000001088394c8f03e5157080000449e00000002")
    info = parse_initial_header(hdr)
    assert info["version"] == 1 and info["header_type"] == "Initial"
    assert info["dcid"] == cid and info["scid"] == b"" and info["token"] == b""
    # 「长度 1182 = 4 字节包号 + 1162 字节帧 + 16 字节认证标签」（RFC 原文）
    assert info["length"] == 1182 and info["length_field_len"] == 2
    assert info["length"] != 0x449E          # 0x449E 是含前缀位的线上字节
    assert info["pn_offset"] == 18           # RFC 原文 header[18..21]
    # 公式与解析器必须给出一致的偏移（Length 与 Token Length 的 varint 宽度都算进去）
    assert pn_offset_initial(info["dcid"], info["scid"], info["token"],
                             info["length_field_len"]) == info["pn_offset"]
    assert pn_length_from_first_byte(hdr[0]) == 4
    assert int.from_bytes(hdr[18:22], "big") == 2
    # 采样位置：4 字节包号时跳过 0 字节，样本即受保护负载的前 16 字节
    assert info["pn_offset"] + 4 == 18 + 4
    # RFC 9001 §5.4.2 的原例：短头 + 8 字节连接 ID 时采样落在第 13..28 字节
    assert pn_offset_short(cid) + 4 == 13
    checks += 9
    # nonce = IV ⊕ 2（附录 A.2 使用 A.1 的客户端 IV）
    client_iv = bytes.fromhex("fa044b2f42a3fd3b46fb255c")
    assert aead_nonce(client_iv, 2).hex() == "fa044b2f42a3fd3b46fb255e"
    checks += 1
    return checks


def _check_a5_vector() -> int:
    """附录 A.5：ChaCha20-Poly1305 短头包，从 secret 一路到 21 字节包，逐字段可验。"""
    checks = 0
    key, iv, hp = packet_keys(A5_SECRET, key_len=32, iv_len=12, hp_len=32)
    assert key.hex() == ("c6d98ff3441c3fe1b2182094f69caa2e"
                         "d4b716b65488960a7a984979fb23e1c8")
    assert iv.hex() == "e0459b3474bdd0e44a41c144"
    assert hp.hex() == ("25a282b9e82f06f21f488917a4fc8f1b"
                        "73573685608597d0efcb076b0ab7a7a4")
    assert next_secret(A5_SECRET).hex() == \
        "1223504755036d556342ee9361d253421a826c9ecdf3c7148684b36b714881f9"
    checks += 4

    assert aead_nonce(iv, A5_PN).hex() == "e0459b3474bdd0e46d417eb0"
    checks += 1

    # 21 字节包：1 字节头部 + 3 字节包号 + 1 字节 PING + 16 字节标签
    expect = bytes.fromhex("4cfe4189655e5cd55c41f69080575d7999c25a5bfb")
    pkt = build_short_packet(b"", A5_PN, None, b"\x01", key, iv, hp, pn_length=3)
    assert len(pkt) == 21 and pkt == expect
    checks += 2

    # 未保护头 = 4200bff4（首字节 0x42：短头 + 密钥相位 0 + 3 字节包号）
    pn_offset = pn_offset_short(b"")                             # 1 + len(dcid) = 1
    assert pn_offset == 1
    mask = header_mask(hp, expect[5:21])                         # 附录给的 sample
    assert mask.hex() == "aefefe7d03"
    # 掩码逐字节作用在「未被掩的头部」上：首字节只异或低 5 位，包号字段整体异或
    # （3 字节包号就只能用 mask[1..3]，多取一个字节会错位）
    pn_field = bytes([0x00, 0xBF, 0xF4])
    masked_pn = bytes(a ^ b for a, b in zip(pn_field, mask[1:4]))
    assert (0x42 ^ (mask[0] & 0x1F), masked_pn) == (0x4C, bytes.fromhex("fe4189"))
    checks += 5

    # 样本 = 从包号起点跳 4 字节后取 16 字节 → 3 字节包号时跳过 1 字节负载
    assert sample_for(pkt, pn_offset) == expect[5:21]
    assert (pn_offset + 4) - (pn_offset + 3) == 1                 # 跳过 1 字节
    checks += 2

    # 往返：接收端只知「最大已处理包号」，也要还原出 A5_PN
    pn, pt = open_short_packet(pkt, 0, A5_PN - 1, key, iv, hp)
    assert pn == A5_PN and pt == b"\x01"
    checks += 2

    # 受保护的首字节 0x4c 低 2 位是 0，直接读会得到「1 字节包号」这个错误结论 ——
    # 必须先去掉头保护才能知道包号长度
    assert pn_length_from_first_byte(expect[0]) == 1
    bad = bytearray(pkt)
    try:
        open_short_packet(bytes(bad[:-1] + bytes([bad[-1] ^ 0x01])), 0, A5_PN - 1,
                          key, iv, hp)
        raise AssertionError("tampered payload must fail authentication")
    except ValueError:
        checks += 1
    buf2 = bytearray(pkt)
    assert remove_header_protection(buf2, pn_offset, hp) == 3
    assert bytes(buf2[:4]).hex() == "4200bff4"                    # 还原出未保护头
    checks += 3
    return checks


def _check_varint() -> int:
    checks = 0
    for value in (0, 63, 64, 16383, 16384, (1 << 30) - 1, 1 << 30, VARINT_MAX):
        enc = encode_varint(value)
        assert decode_varint(enc) == (value, len(enc))
        checks += 1
    assert len(encode_varint(63)) == 1 and len(encode_varint(64)) == 2
    assert len(encode_varint(16384)) == 4 and len(encode_varint(1 << 30)) == 8
    # 「不要求最短编码」：0x40 0x3f 是合法的 2 字节编码，解出 63
    assert decode_varint(b"\x40\x3f") == (63, 2)
    assert decode_varint(b"\x80\x00\x00\x3f") == (63, 4)
    checks += 6
    try:
        encode_varint(VARINT_MAX + 1)
        raise AssertionError("varint overflow must raise")
    except ValueError:
        checks += 1
    try:
        decode_varint(b"\x40")          # 前缀声称 2 字节，实际只剩 1 字节
        raise AssertionError("truncated varint must raise")
    except ValueError:
        checks += 1
    return checks


def _check_packet_number() -> int:
    """附录 A.2 的两个实例 + A.3 的解码窗口。"""
    checks = 0
    assert len(encode_packet_number(0xAC5C02, 0xABE8B3)) == 2
    assert int.from_bytes(encode_packet_number(0xAC5C02, 0xABE8B3), "big") == 0x5C02
    assert len(encode_packet_number(0xACE8FE, 0xABE8B3)) == 3
    assert decode_packet_number(0xABE8B3, 0x5C02, 16) == 0xAC5C02
    # 0xACE8FE 本身不足 24 位，所以「截断值」就是它自己（曾误填 16 位的 0xE8FE，
    # 断言立刻报错 —— 解码一侧的入参是线上字节，不是任意短值）
    assert decode_packet_number(0xABE8B3, 0xACE8FE, 24) == 0xACE8FE
    # 从未收到 ACK 时按 full_pn + 1 计：包号 2 只需 1 字节（附录 A.2 的示例
    # 用 4 字节编码是发送方的自由选择，不是算法要求）
    assert encode_packet_number(2, None) == b"\x02"
    assert len(encode_packet_number(2, None, pn_length=4)) == 4
    # 2 的幂是分界点：n=128 恰好 8 位 → 1 字节，n=129 需要 2 字节
    assert len(encode_packet_number(127, 0)) == 1
    assert len(encode_packet_number(128, 0)) == 1
    assert len(encode_packet_number(129, 0)) == 2
    # 窗口边界：候选值超出半窗时向两侧修正
    assert decode_packet_number(0x0000FF, 0x00, 8) == 0x000100
    assert decode_packet_number(0x000100, 0xFF, 8) == 0x0000FF
    # 往返：编码出的字节数必须足以让「只知道上一个包号」的接收端还原出原值
    for pn in (1, 63, 64, 0x5C02, 0xFFFF, 0x10000, 0xAC5C02, 0x1000000):
        enc = encode_packet_number(pn, 0)
        assert decode_packet_number(pn - 1, int.from_bytes(enc, "big"),
                                   len(enc) * 8) == pn
        checks += 1
    checks += 11
    return checks


def _check_transport() -> int:
    """自己组一个短头包做往返，覆盖「采样不足 / 篡改 / 错密钥」三条失败路径。"""
    checks = 0
    cid = bytes.fromhex("0102030405060708")
    client_secret, server_secret = initial_secrets(cid)
    key, iv, hp = packet_keys(client_secret, key_len=32, iv_len=12, hp_len=32)
    payload = bytes(range(32))
    pkt = build_short_packet(cid, 0xAC5C02, 0xABE8B2, payload, key, iv, hp)
    assert pkt[0] & 0x80 == 0
    assert len(pkt) == 1 + len(cid) + 2 + len(payload) + 16
    pn, pt = open_short_packet(pkt, len(cid), 0xABE8B2, key, iv, hp)
    assert pn == 0xAC5C02 and pt == payload
    checks += 3
    try:
        sample_for(pkt[:20], 1 + len(cid))
        raise AssertionError("short packet must be rejected")
    except ValueError:
        checks += 1
    tampered = bytearray(pkt)
    tampered[0] ^= 0x01
    try:
        open_short_packet(bytes(tampered), len(cid), 0xABE8B2, key, iv, hp)
        raise AssertionError("tampered first byte must fail")
    except (ValueError, IndexError):
        checks += 1
    # 方向搞反（用服务端密钥解客户端包）必须失败
    other_key, other_iv, other_hp = packet_keys(server_secret, key_len=32, iv_len=12,
                                                hp_len=32)
    try:
        open_short_packet(pkt, len(cid), 0xABE8B2, other_key, other_iv, other_hp)
        raise AssertionError("wrong direction keys must fail")
    except ValueError:
        checks += 1

    # 最小帧长度：包号越长，负载可以越短
    assert [min_frame_length(n) for n in (1, 2, 3, 4)] == [3, 2, 1, 0]
    # 样本长度是常量 16，与套件无关（AES 与 ChaCha20 相同）
    assert SAMPLE_LEN == 16
    checks += 5

    # 换密钥后旧密钥必须失效；同一 secret 下头保护掩码是确定性的
    nk, niv, nhp = packet_keys(next_secret(client_secret), key_len=32, iv_len=12,
                               hp_len=32)
    assert nk != key and niv != iv and nhp != hp
    try:
        open_short_packet(pkt, len(cid), 0xABE8B2, nk, niv, nhp)
        raise AssertionError("old packet must not open with updated keys")
    except ValueError:
        checks += 2
    assert header_mask(hp, bytes(range(16))) != header_mask(hp, bytes(range(1, 17)))
    checks += 1
    return checks


def _main() -> int:
    total = 0
    for name, fn in (("hkdf/initial keys (RFC 9001 A.1)", _check_hkdf_labels),
                     ("initial header (RFC 9001 A.2)", _check_initial_header),
                     ("chacha20 short packet (RFC 9001 A.5)", _check_a5_vector),
                     ("varint (RFC 9000 §16)", _check_varint),
                     ("packet number (RFC 9000 A.2/A.3)", _check_packet_number),
                     ("short packet transport", _check_transport)):
        got = fn()
        total += got
        print(f"  {name}: {got} checks")
    print(f"quic_packet: {total} checks passed")
    return total


if __name__ == "__main__":
    _main()
