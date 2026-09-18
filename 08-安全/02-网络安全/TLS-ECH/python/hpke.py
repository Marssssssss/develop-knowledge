"""
HPKE base 模式（RFC 9180）—— DHKEM(X25519, HKDF-SHA256) + HKDF-SHA256 + ChaCha20-Poly1305

ECH 用 HPKE 的 **base mode** 加密 ClientHelloInner（RFC 9849 §4 的 HpkeKeyConfig 里
kem_id/kdf_id/aead_id 就取自 RFC 9180）。本文件实现 RFC 9180 §4-§7 的最小完备子集：

    §4  DHKEM(X25519, HKDF-SHA256)   派生密钥对 / Encap / Decap
    §5  Key schedule                 key_schedule_context / secret / key / base_nonce / exporter_secret
    §6  Context                       Seal / Open / Export
    §7  SetupBaseS / SetupBaseR

**最容易被抄错的地方**是 RFC 9180 §4 的「标签化」：KEM 与 HPKE 各自有一条 suite_id，
且 LabeledExtract 把 ikm 拼在最后、LabeledExpand 把 L 编码成 2 字节拼在最前。
顺序错一位，密钥就完全不对，而且不会有任何报错 —— 只能靠附录 A.2 的官方向量抓出来。

X25519 也是自己写的（§5.2 的 Montgomery 阶梯）：Python 标准库没有 X25519，
而哈希、HMAC、HMAC 都在标准库里，所以这一块是「唯一必须手写」的密码学原语。
"""

from __future__ import annotations

import hashlib
import hmac
import os

from hpke_aead import KEY_LEN, NONCE_LEN, TAG_LEN, open_, seal

# ------------------------------------------------------------ 常量（RFC 9180 §7.1）

KEM_ID_X25519 = 0x0020
KDF_ID_HKDF_SHA256 = 0x0001
AEAD_ID_CHACHA20POLY1305 = 0x0003

NH = 32          # SHA-256 输出长度
NK = KEY_LEN     # ChaCha20-Poly1305 密钥 32 字节
NN = NONCE_LEN   # nonce 12 字节
NSECRET = 32     # DHKEM 的 shared_secret 长度
NENC = 32        # X25519 封装密钥长度
NSK = 32
NPK = 32

MODE_BASE = 0x00

P25519 = 2 ** 255 - 19
X25519_BASE = (9).to_bytes(32, "little")

_KEM_SUITE_ID = b"KEM" + KEM_ID_X25519.to_bytes(2, "big")
_HPKE_SUITE_ID = (b"HPKE" + KEM_ID_X25519.to_bytes(2, "big")
                  + KDF_ID_HKDF_SHA256.to_bytes(2, "big")
                  + AEAD_ID_CHACHA20POLY1305.to_bytes(2, "big"))


class HpkeError(Exception):
    """HPKE 层错误（Open 认证失败、密钥长度不对等）"""


# ------------------------------------------------------------ HKDF（RFC 5869）

def extract(salt: bytes, ikm: bytes) -> bytes:
    """RFC 9180 §5.1：salt 为空串时按 Hash.length 个零字节处理。"""
    if len(salt) == 0:
        salt = b"\x00" * NH
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def expand(prk: bytes, info: bytes, length: int) -> bytes:
    out, prev, counter = b"", b"", 1
    while len(out) < length:
        prev = hmac.new(prk, prev + info + bytes([counter]), hashlib.sha256).digest()
        out += prev
        counter += 1
    return out[:length]


def labeled_extract(salt: bytes, suite_id: bytes, label: bytes, ikm: bytes) -> bytes:
    """LabeledExtract：b"HPKE-v1" | suite_id | label | ikm 全部塞进 ikm 位置。"""
    return extract(salt, b"HPKE-v1" + suite_id + label + ikm)


def labeled_expand(prk: bytes, suite_id: bytes, label: bytes, info: bytes,
                   length: int) -> bytes:
    """LabeledExpand：L 编码成 2 字节大端放在 info 最前面。"""
    return expand(prk, length.to_bytes(2, "big") + b"HPKE-v1" + suite_id + label + info,
                  length)


# ------------------------------------------------------------ X25519（RFC 7748 §5）

def _clamp(k: bytes) -> int:
    b = bytearray(k)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return int.from_bytes(b, "little")


def x25519(scalar: bytes, u_coord: bytes) -> bytes:
    """Montgomery 阶梯；返回 32 字节小端 u 坐标。零结果（低阶点）由调用方判掉。"""
    if len(scalar) != 32 or len(u_coord) != 32:
        raise HpkeError("X25519 需要 32 字节输入")
    k = _clamp(scalar)
    x1 = int.from_bytes(u_coord, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3 = 1, 0, x1, 1
    swap = 0
    for t in reversed(range(255)):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        a = (x2 + z2) % P25519
        aa = a * a % P25519
        b = (x2 - z2) % P25519
        bb = b * b % P25519
        e = (aa - bb) % P25519
        c = (x3 + z3) % P25519
        d = (x3 - z3) % P25519
        da = d * a % P25519
        cb = c * b % P25519
        x3 = pow(da + cb, 2, P25519)
        z3 = x1 * pow(da - cb, 2, P25519) % P25519
        x2 = aa * bb % P25519
        z2 = e * ((aa + 121665 * e) % P25519) % P25519
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return (x2 * pow(z2, P25519 - 2, P25519) % P25519).to_bytes(32, "little")


# ------------------------------------------------------------ DHKEM（RFC 9180 §4.1）

def kem_extract_and_expand(dh: bytes, kem_context: bytes) -> bytes:
    eae_prk = labeled_extract(b"", _KEM_SUITE_ID, b"eae_prk", dh)
    return labeled_expand(eae_prk, _KEM_SUITE_ID, b"shared_secret", kem_context, NSECRET)


def derive_key_pair(ikm: bytes) -> tuple[bytes, bytes]:
    """RFC 9180 §7.1.3：X25519 不做拒绝采样，直接 LabeledExpand 出 sk。"""
    dkp_prk = labeled_extract(b"", _KEM_SUITE_ID, b"dkp_prk", ikm)
    sk = labeled_expand(dkp_prk, _KEM_SUITE_ID, b"sk", b"", NSK)
    return sk, x25519(sk, X25519_BASE)


def encap(pk_r: bytes, ikm: bytes | None = None) -> tuple[bytes, bytes]:
    """返回 (enc, shared_secret)。ikm 给定则走 DeriveKeyPair（测试向量需要）。"""
    if ikm is None:
        sk_e = os.urandom(32)
        pk_e = x25519(sk_e, X25519_BASE)
    else:
        sk_e, pk_e = derive_key_pair(ikm)
    enc = pk_e
    dh = x25519(sk_e, pk_r)
    if dh == b"\x00" * 32:
        raise HpkeError("X25519 结果为低阶点")
    return enc, kem_extract_and_expand(dh, enc + pk_r)


def decap(enc: bytes, sk_r: bytes, pk_r: bytes) -> bytes:
    dh = x25519(sk_r, enc)
    if dh == b"\x00" * 32:
        raise HpkeError("X25519 结果为低阶点")
    return kem_extract_and_expand(dh, enc + pk_r)


# ------------------------------------------------------------ 密钥计划（§5）

class KeySchedule:
    """由 (mode, shared_secret, info) 派生 key / base_nonce / exporter_secret。"""

    def __init__(self, shared_secret: bytes, info: bytes) -> None:
        psk_id_hash = labeled_extract(b"", _HPKE_SUITE_ID, b"psk_id_hash", b"")
        info_hash = labeled_extract(b"", _HPKE_SUITE_ID, b"info_hash", info)
        self.key_schedule_context = bytes([MODE_BASE]) + psk_id_hash + info_hash
        self.secret = labeled_extract(shared_secret, _HPKE_SUITE_ID, b"secret", b"")
        self.key = labeled_expand(self.secret, _HPKE_SUITE_ID, b"key",
                                  self.key_schedule_context, NK)
        self.base_nonce = labeled_expand(self.secret, _HPKE_SUITE_ID, b"base_nonce",
                                         self.key_schedule_context, NN)
        self.exporter_secret = labeled_expand(self.secret, _HPKE_SUITE_ID, b"exp",
                                              self.key_schedule_context, NH)


class Context:
    """HPKE 加密上下文：序号单调递增，nonce = base_nonce XOR 序号。"""

    def __init__(self, ks: KeySchedule) -> None:
        self.ks = ks
        self.seq = 0

    def _nonce(self) -> bytes:
        return bytes(a ^ b for a, b in zip(self.ks.base_nonce, self.seq.to_bytes(NN, "big")))

    def seal(self, aad: bytes, plaintext: bytes) -> bytes:
        out = seal(self.ks.key, self._nonce(), aad, plaintext)
        self.seq += 1
        return out

    def open(self, aad: bytes, sealed: bytes) -> bytes:
        try:
            out = open_(self.ks.key, self._nonce(), aad, sealed)
        except ValueError as exc:
            raise HpkeError(f"HPKE Open 失败：{exc}") from exc
        self.seq += 1
        return out

    def export(self, exporter_context: bytes, length: int) -> bytes:
        return labeled_expand(self.ks.exporter_secret, _HPKE_SUITE_ID, b"sec",
                              exporter_context, length)


def setup_base_s(pk_r: bytes, info: bytes, ikm: bytes | None = None):
    """发送方：返回 (enc, Context)。"""
    enc, shared_secret = encap(pk_r, ikm)
    return enc, Context(KeySchedule(shared_secret, info))


def setup_base_r(enc: bytes, sk_r: bytes, pk_r: bytes, info: bytes) -> Context:
    """接收方：返回 Context。"""
    return Context(KeySchedule(decap(enc, sk_r, pk_r), info))


def sealed_size(plaintext_len: int) -> int:
    """ECH 需要「先算出密文长度、填进占位 payload 再算 AAD」（RFC 9849 §6.1.1）。"""
    return plaintext_len + TAG_LEN
