"""
QUIC v1 的包格式与包号编解码（RFC 9000 §16 变长整数 / §17.1 包号 / §17.2 长头 / 附录 A.2·A.3）

本文件只做「不需要密钥」的部分：
  1. 变长整数（varint）：2 位前缀决定 1/2/4/8 字节，且**不要求最短编码**
  2. 长头/短头布局与 pn_offset —— 头保护的采样位置完全依赖它
  3. 包号编解码：编码侧按「未确认包数的两倍」挑字节数，解码侧按半窗口还原完整包号
  4. AEAD nonce 与「最小帧长度」这两条由规范直接规定的小规则

头保护与短头包的收发在 quic_protect.py；本文件的实例断言在 quic_test.py。
"""

from __future__ import annotations

import struct

# ------------------------------------------------------------
# 1. 变长整数（RFC 9000 §16）
# ------------------------------------------------------------

VARINT_MAX = (1 << 62) - 1


def encode_varint(value: int, min_len: int = 0) -> bytes:
    """按最小长度编码；min_len 用于「长度字段必须至少同宽」的场合（如 Initial 的 Length）。

    与多数整数字段不同，QUIC **不要求**最短编码（唯一例外是 Frame Type 字段），
    所以解码器除了前缀决定的宽度之外，不能做任何「规范性」检查。
    """
    if value < 0 or value > VARINT_MAX:
        raise ValueError("varint out of range")
    if min_len <= 1 and value < 1 << 6:
        return bytes([value])
    if min_len <= 2 and value < 1 << 14:
        return struct.pack("!H", value | 0x4000)
    if min_len <= 4 and value < 1 << 30:
        return struct.pack("!I", value | 0x80000000)
    return struct.pack("!Q", value | 0xC000000000000000)


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    """返回 (值, 消耗字节数)。前缀 00/01/10/11 分别对应 1/2/4/8 字节。"""
    if offset >= len(data):
        raise ValueError("varint truncated")
    length = 1 << (data[offset] >> 6)
    if offset + length > len(data):
        raise ValueError("varint truncated")
    raw = data[offset:offset + length]
    if length == 1:
        return raw[0] & 0x3F, 1
    return int.from_bytes(raw, "big") & ((1 << (8 * length - 2)) - 1), length


# ------------------------------------------------------------
# 2. 头部布局与 pn_offset
# ------------------------------------------------------------

LONG_HEADER_TYPES = {0: "Initial", 1: "0-RTT", 2: "Handshake", 3: "Retry"}


def pn_offset_initial(dcid: bytes, scid: bytes, token: bytes,
                      length_field_len: int) -> int:
    """长头首字节到包号字段起点的字节数。

    组成：1（首字节）+ 4（Version）+ 1（DCID Len）+ DCID + 1（SCID Len）+ SCID
        + token_len 的 varint 宽度 + Token + Length 字段宽度。

    注意 Length 与 Token Length 都是 varint，**它们自身占几个字节也参与计算** ——
    这正是 RFC 9001 强调「不能假设包号固定在某个偏移」的原因，也是头保护采样
    必须先解析头部才能定位的原因。
    """
    return 7 + len(dcid) + len(scid) + len(encode_varint(len(token))) + \
        len(token) + length_field_len


def pn_offset_short(dcid: bytes) -> int:
    """短头：1 字节首字节 + DCID，后面紧跟包号（没有长度字段）。"""
    return 1 + len(dcid)


def parse_initial_header(pkt: bytes) -> dict:
    """解析长头 Initial 的**未保护**部分，返回各字段解出的值与其偏移。

    这里按规范顺序逐段解码而不是按固定偏移切片：一旦 Token 或 Length 用了
    更宽的 varint 编码，固定偏移就会错位。
    """
    if len(pkt) < 7 or pkt[0] & 0x80 == 0:
        raise ValueError("not a long header packet")
    version = struct.unpack("!I", pkt[1:5])[0]
    dcid_len = pkt[5]
    dcid = pkt[6:6 + dcid_len]
    off = 6 + dcid_len
    scid_len = pkt[off]
    scid = pkt[off + 1:off + 1 + scid_len]
    off += 1 + scid_len
    token_len, n = decode_varint(pkt, off)
    off += n
    token = pkt[off:off + token_len]
    off += token_len
    length, n = decode_varint(pkt, off)
    off += n
    return {
        "first_byte": pkt[0], "version": version, "dcid": dcid, "scid": scid,
        "token": token, "length": length, "length_field_len": n,
        "pn_offset": off, "header_type": LONG_HEADER_TYPES.get((pkt[0] & 0x30) >> 4),
    }


def pn_length_from_first_byte(first_byte: int) -> int:
    """首字节最低 2 位 = 包号长度 - 1，故取值范围恒为 1..4。"""
    return (first_byte & 0x03) + 1


# ------------------------------------------------------------
# 3. 包号编解码（RFC 9000 §17.1 + 附录 A.2/A.3）
# ------------------------------------------------------------

def _min_bits(num_unacked: int) -> int:
    """RFC 9000 附录 A.2 里 `log(num_unacked, 2) + 1` 的整数等价形式。

    伪代码用的是**实数** log，所以 n 恰为 2 的幂时 min_bits 正好落在整数上、
    不再多一位；直接写 `bit_length() + 1` 会在这些点上多要一个字节
    （例：n=128 规范算得 8 位即 1 字节，粗暴写法算得 9 位即 2 字节）。
    """
    b = num_unacked.bit_length()
    return b if num_unacked == 1 << (b - 1) else b + 1


def encode_packet_number(full_pn: int, largest_acked: int | None,
                         pn_length: int | None = None) -> bytes:
    """挑包号编码字节数并截断到低位。

    规范要求「足以表示未确认区间两倍以上的范围」；附录 A.2 给了示例算法：
    未确认包数 = full_pn - largest_acked（从未收到 ACK 时取 full_pn + 1）。
    pn_length 是发送方的自由选择（只要不小于上述下限），用来复现规范示例里的
    固定 3/4 字节编码。
    """
    if pn_length is None:
        num_unacked = full_pn + 1 if largest_acked is None else full_pn - largest_acked
        pn_length = max(1, -(-_min_bits(num_unacked) // 8))
    if not 1 <= pn_length <= 4:
        raise ValueError("packet number length must be 1..4")
    return full_pn.to_bytes(4, "big")[-pn_length:]


def decode_packet_number(largest_pn: int, truncated_pn: int, pn_nbits: int) -> int:
    """按窗口还原完整包号（附录 A.3）：候选值必须落在期望值的半窗口内。

    对比特流的解读必须自洽：接收端只知道「最大已处理包号」和截断值，
    任何超出半窗的候选都要向两侧修正，否则会出现包号回跳。
    """
    expected_pn = largest_pn + 1
    pn_win = 1 << pn_nbits
    pn_hwin = pn_win // 2
    pn_mask = pn_win - 1
    candidate = (expected_pn & ~pn_mask) | truncated_pn
    if candidate <= expected_pn - pn_hwin and candidate < (1 << 62) - pn_win:
        return candidate + pn_win
    if candidate > expected_pn + pn_hwin and candidate >= pn_win:
        return candidate - pn_win
    return candidate


# ------------------------------------------------------------
# 4. nonce 与最小帧长度
# ------------------------------------------------------------

def aead_nonce(iv: bytes, packet_number: int) -> bytes:
    """nonce = IV ⊕ (62 位包号左填零到 IV 长度)，按网络字节序逐字节异或。

    用异或而不是拼接，是为了让 IV 起到「每条连接一个偏移」的作用：即使两条
    连接用了同一密钥（正常不会），nonce 也不会碰撞。
    """
    pn = packet_number.to_bytes(8, "big")[2:]           # 只保留低 62 位
    padded = b"\x00" * (len(iv) - len(pn)) + pn
    return bytes(a ^ b for a, b in zip(iv, padded))


def min_frame_length(pn_length: int, expansion: int = 16, sample_len: int = 16,
                     offset: int = 4) -> int:
    """明文帧至少要多少字节，才能保证能取出头保护样本。

    约束是 pn_length + expansion + frame_len ≥ offset + sample_len
    （offset = 4 是因为采样从包号字段起点往后跳 4 字节，按最长包号预留）。
    1-RTT 的 AEAD 扩展固定 16 字节，于是：1 字节包号需 3 字节帧、
    2 字节包号需 2 字节、3 字节包号需 1 字节、4 字节包号可以不带帧数据。
    """
    return max(0, offset + sample_len - pn_length - expansion)
