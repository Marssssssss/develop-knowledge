"""WireGuard 的防 DoS cookie 与传输层（数据消息、重放窗口、定时器）

消息级 MAC1/MAC2 在 wg_noise.py 里（握手消息构造要用到），本模块只管 cookie 本身
以及握手完成后的数据路径。
"""

from __future__ import annotations

import os
import struct

from wg_crypto import (aead_decrypt, aead_encrypt,
                       xchacha20poly1305_decrypt, xchacha20poly1305_encrypt)
from wg_kdf import blake2s

from wg_noise import (COOKIE_LABEL, COOKIE_LEN, COOKIE_NONCE_LEN, COUNTER_BITS_TOTAL,
                      COUNTER_WINDOW_SIZE, LEN_COOKIE, LEN_DATA_HEADER, M_COOKIE,
                      M_DATA, PADDING_MULTIPLE, REJECT_AFTER_TIME,
                      REKEY_AFTER_MESSAGES, REKEY_AFTER_TIME, _header,
                      check_mac1, macs_for)


# ------------------------------------------------------------
# 4. 防 DoS：MAC1 / MAC2 / cookie
# ------------------------------------------------------------

def cookie_encryption_key(responder_static_pub: bytes) -> bytes:
    """cookie 的加密密钥 = BLAKE2s(\"cookie--\" ‖ 响应方静态公钥)（内核 precompute_key）。"""
    return blake2s(COOKIE_LABEL + responder_static_pub)


def make_cookie(secret: bytes, src_ip: bytes, src_port: int) -> bytes:
    """cookie = keyed-BLAKE2s(secret, 源 IP ‖ 源端口, 16B)。

    绑的是「源地址」而不是消息内容，因此 cookie 无法被搬到别处重放；
    secret 每 COOKIE_SECRET_MAX_AGE=120 s 轮换一次（内核 cookie.c）。
    """
    return blake2s(src_ip + struct.pack("!H", src_port), COOKIE_LEN, secret)


def cookie_reply(initiation: bytes, receiver_index: int, secret: bytes,
                 responder_static_pub: bytes, src_ip: bytes, src_port: int
                 ) -> tuple[bytes, bytes]:
    """构造 MESSAGE_HANDSHAKE_COOKIE，返回 (消息, 明文 cookie)。

    cookie 用 XChaCha20Poly1305 加密，密钥 = BLAKE2s(\"cookie--\" ‖ 响应方静态公钥)，
    AD 取发起消息里的 MAC1 —— 这使「谁能算出该 MAC1，谁才能解开 cookie」，
    因为 MAC1 本身以响应方静态公钥为密钥。nonce 每次随机 24 字节。
    """
    cookie = make_cookie(secret, src_ip, src_port)
    nonce = os.urandom(COOKIE_NONCE_LEN)
    mac1 = initiation[-32:-16]
    sealed = xchacha20poly1305_encrypt(cookie_encryption_key(responder_static_pub),
                                       nonce, mac1, cookie)
    msg = _header(M_COOKIE) + struct.pack("<I", receiver_index) + nonce + sealed
    return msg, cookie


def open_cookie(msg: bytes, responder_static_pub: bytes, mac1: bytes) -> bytes:
    if len(msg) != LEN_COOKIE:
        raise ValueError("bad cookie length")
    nonce = msg[8:8 + COOKIE_NONCE_LEN]
    return xchacha20poly1305_decrypt(cookie_encryption_key(responder_static_pub),
                                     nonce, mac1, msg[8 + COOKIE_NONCE_LEN:])


# ------------------------------------------------------------
# 5. 传输数据与重放计数
# ------------------------------------------------------------

def nonce_for(counter: int) -> bytes:
    """ChaCha20-Poly1305 的 12 字节 nonce = 4 字节零 ‖ 8 字节小端计数器。"""
    return b"\x00" * 4 + struct.pack("<Q", counter)


def data_message(key: bytes, counter: int, plain: bytes, key_idx: int = 0) -> bytes:
    """type=4 ‖ key_idx ‖ counter ‖ AEAD(AD 为空)。明文向上取整到 16 字节再加密。"""
    body = plain + b"\x00" * ((-len(plain)) % PADDING_MULTIPLE)
    return (_header(M_DATA) + struct.pack("<I", key_idx) +
            struct.pack("<Q", counter) + aead_encrypt(key, nonce_for(counter), b"", body))


def open_data(key: bytes, counter: int, msg: bytes) -> bytes:
    return aead_decrypt(key, nonce_for(counter), b"", msg[LEN_DATA_HEADER:])


def keypair_expired(birth: float, now: float, messages: int) -> bool:
    """密钥对失效：时间超 REJECT_AFTER_TIME(180s) 或消息数超 U64_MAX - 窗口 - 1。"""
    return (now - birth) > REJECT_AFTER_TIME or \
        messages > (2**64 - 1) - COUNTER_WINDOW_SIZE - 1


def should_rekey(birth: float, now: float, messages: int) -> bool:
    """主动重密钥的触发条件（REKEY_AFTER_TIME=120s 或 2^60 条消息）。"""
    return (now - birth) > REKEY_AFTER_TIME or messages > REKEY_AFTER_MESSAGES


class ReplayCounter:
    """内核 receive.c counter_validate：8192 位滑动窗口（含 64 位冗余避免移位越界）。"""

    def __init__(self) -> None:
        self.bits = bytearray(COUNTER_BITS_TOTAL // 8)
        self.last: int | None = None

    def validate(self, counter: int) -> bool:
        if self.last is None:
            self.last = counter
        elif counter > self.last:
            if counter - self.last > COUNTER_WINDOW_SIZE:
                self.bits = bytearray(COUNTER_BITS_TOTAL // 8)   # 整体跳过 → 历史清零
            self.last = counter
        elif self.last - counter >= COUNTER_WINDOW_SIZE + 1:
            return False                              # 落在窗口之外：直接拒
        idx = counter % COUNTER_BITS_TOTAL
        byte, bit = idx // 8, 1 << (idx % 8)
        if self.bits[byte] & bit:
            return False                              # 窗口内重复：拒
        self.bits[byte] |= bit
        return True
