"""
QUIC v1 的头保护与短头包收发（RFC 9001 §5.3 nonce / §5.4 头保护 / §5.5 接收受保护包
+ RFC 9000 §17.3 短头包格式）

头保护（header protection）是 QUIC 相对 TLS 的一个独有设计：**包头的关键位也被加密**，
这样链路上的观察者既看不到包号（无法做流量分析），也看不到「包号字段有多长」。
代价是采样位置必须与被保护的位无关 —— 所以采样从**包号字段起点往后跳 4 字节**取
16 字节，而不是从「包号字段末尾」取（包号长度本身是被保护的）。
"""

from __future__ import annotations

from quic_crypto import aead_open, aead_seal, header_mask
from quic_packet import (aead_nonce, decode_packet_number, encode_packet_number,
                         pn_length_from_first_byte, pn_offset_short)

# ------------------------------------------------------------
# 1. 头保护（RFC 9001 §5.4）
# ------------------------------------------------------------

SAMPLE_LEN = 16
MASK_LEN = 5
SAMPLE_OFFSET_FROM_PN = 4


def sample_for(pkt: bytes, pn_offset: int) -> bytes:
    """从「包号字段起点 + 4」取 16 字节。

    长度不足时必须**丢弃整包**而不是勉强计算 —— 勉强算出来的掩码会把首字节
    解成随机的包号长度，接收端随后的 AEAD 必然失败，但白耗一次解密、还可能
    变成放大攻击的资源消耗点。
    """
    start = pn_offset + SAMPLE_OFFSET_FROM_PN
    if start + SAMPLE_LEN > len(pkt):
        raise ValueError("packet too short for header protection sample")
    return pkt[start:start + SAMPLE_LEN]


def _mask(pkt: bytes, pn_offset: int, hp_key: bytes) -> bytes:
    return header_mask(hp_key, sample_for(pkt, pn_offset))


def apply_header_protection(pkt: bytearray, pn_offset: int, pn_length: int,
                            hp_key: bytes) -> None:
    """就地加密头部：首字节低 4 位（长头）或低 5 位（短头），加上包号的全部字节。

    长头只掩 4 位是因为最低 2 位（包号长度）之外的 2 位（类型）和最高位（固定位）
    需要保留；短头要掩 5 位，因为留下 3 位（固定位 + 密钥相位）正好。
    """
    mask = _mask(bytes(pkt), pn_offset, hp_key)
    pkt[0] ^= mask[0] & (0x0F if pkt[0] & 0x80 else 0x1F)
    for i in range(pn_length):
        pkt[pn_offset + i] ^= mask[1 + i]


def remove_header_protection(pkt: bytearray, pn_offset: int, hp_key: bytes) -> int:
    """去掉头保护并返回解出的包号长度。

    关键顺序：先用**只依赖偏移**的采样算掩码 → 还原首字节 → 才知道包号多长 →
    再还原包号字节。反过来做是做不到的，因为恢复采样位置所需的偏移信息里
    本来就含有被加密的位。
    """
    mask = _mask(bytes(pkt), pn_offset, hp_key)
    pkt[0] ^= mask[0] & (0x0F if pkt[0] & 0x80 else 0x1F)
    pn_length = pn_length_from_first_byte(pkt[0])
    for i in range(pn_length):
        pkt[pn_offset + i] ^= mask[1 + i]
    return pn_length


# ------------------------------------------------------------
# 2. 组装 / 解析一个完整的 1-RTT 短头包（ChaCha20 套件）
# ------------------------------------------------------------

def build_short_packet(dcid: bytes, packet_number: int, largest_acked: int | None,
                       payload: bytes, key: bytes, iv: bytes, hp_key: bytes,
                       key_phase: int = 0, pn_length: int | None = None) -> bytes:
    """AAD 是**未保护**的完整头（含包号明文），因此必须先定好包号编码再加密。"""
    pn_bytes = encode_packet_number(packet_number, largest_acked, pn_length)
    first = 0x40 | (key_phase << 2) | (len(pn_bytes) - 1)
    header = bytes([first]) + dcid + pn_bytes
    ct = aead_seal(key, aead_nonce(iv, packet_number), header, payload)
    pkt = bytearray(header + ct)
    apply_header_protection(pkt, 1 + len(dcid), len(pn_bytes), hp_key)
    return bytes(pkt)


def open_short_packet(pkt: bytes, dcid_len: int, largest_pn: int,
                      key: bytes, iv: bytes, hp_key: bytes) -> tuple[int, bytes]:
    """返回 (完整包号, 明文)。解析顺序：先去头保护，再解 AEAD。

    顺序错了会解不开：AEAD 的 AAD 是「去保护后的头」，而头本身只有在去掉
    头保护之后才是明文。
    """
    buf = bytearray(pkt)
    pn_offset = pn_offset_short(pkt[1:1 + dcid_len])
    pn_length = remove_header_protection(buf, pn_offset, hp_key)
    truncated = int.from_bytes(buf[pn_offset:pn_offset + pn_length], "big")
    pn = decode_packet_number(largest_pn, truncated, pn_length * 8)
    header = bytes(buf[:pn_offset + pn_length])
    return pn, aead_open(key, aead_nonce(iv, pn), header, bytes(buf[pn_offset + pn_length:]))
