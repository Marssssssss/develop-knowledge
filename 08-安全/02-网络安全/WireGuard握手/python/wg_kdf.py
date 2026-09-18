"""
WireGuard 的密钥派生层：BLAKE2s / HMAC-BLAKE2s / KDF / TAI64N 时间戳

WireGuard 与纯 Noise 的差别之一就在这一层：
  - Noise 的 HKDF 用协议名段指定的哈希；WireGuard 把哈希整体换成 BLAKE2s
    （内核 noise.c 的 hmac() 与 kdf() 就是「HMAC-BLAKE2s 上的 HKDF」）
  - 内核 kdf() 一次可产出 1~3 个输出，用途各不相同（见 kdf() 文档字符串），
    这是读 Noise 规范时最容易漏的一处，因为 RFC 形式只写「HKDF(ck, ikm, n)」
  - 时间戳用 TAI64N（12 字节），且纳秒被向下对齐到 2^24 以限制精度泄露

标准库 hashlib.blake2s 已支持 digest_size 与 key 参数，语义与内核
blake2s(out, outlen, key, keylen, in, inlen) 一致。
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import struct
import time

BLAKE2S_LEN = 32
BLAKE2S_BLOCK = 64
HASH_LEN = BLAKE2S_LEN      # Noise 里的 h/ck 长度
SYM_LEN = 32                # ChaCha20Poly1305 密钥长度


def blake2s(data: bytes, outlen: int = BLAKE2S_LEN, key: bytes = b"") -> bytes:
    """BLAKE2s：outlen 可取 1..32；key 非空即 keyed 模式（内核用它做 MAC1/MAC2）。"""
    return hashlib.blake2s(data, digest_size=outlen, key=key).digest()


def hmac_blake2s(key: bytes, msg: bytes) -> bytes:
    """HMAC-BLAKE2s：块长 64（BLAKE2S_BLOCK），超块长的密钥先哈希一次。"""
    k = blake2s(key) if len(key) > BLAKE2S_BLOCK else key
    k = k + b"\x00" * (BLAKE2S_BLOCK - len(k))
    return blake2s(bytes(a ^ 0x5C for a in k) +
                   blake2s(bytes(a ^ 0x36 for a in k) + msg))


def kdf(chaining_key: bytes, data: bytes,
        n1: int = BLAKE2S_LEN, n2: int = 0, n3: int = 0) -> tuple[bytes, ...]:
    """HKDF(BLAKE2s)：先 Extract 再按 0x01/0x02/0x03 顺序 Expand，可产出 1~3 个输出。

    对应内核 kdf(first_dst, second_dst, third_dst, data, first_len, second_len,
    third_len, data_len, chaining_key)，三种调用形态在本 demo 中都会用到：
      - kdf(ck, e.public_key, 32)          → 只取新 ck（message_ephemeral）
      - kdf(ck, dh, 32, 32)                → 新 ck + 新 k（mix_dh / Split / derive_keys）
      - kdf(ck, psk, 32, 32, 32)           → 新 ck + temp_h + 新 k（mix_psk）
    """
    secret = hmac_blake2s(chaining_key, data)
    outs, prev = [], b""
    lengths = (n1, n2, n3)
    for i in (1, 2, 3):
        if lengths[i - 1] == 0:
            break
        prev = hmac_blake2s(secret, prev + bytes([i]))
        outs.append(prev[:lengths[i - 1]])
    return tuple(outs)


# TAI64N：8 字节秒（偏移 0x400000000000000A）+ 4 字节纳秒 = 12 字节（cr.yp.to/tai64）。
# 内核把纳秒向下对齐到 rounddown_pow_of_two(1e9 / INITIATIONS_PER_SECOND)，
# 即 1e9/50 = 2e7 → 2^24 = 16,777,216 ns（≈16.78 ms），目的是不让精确计时泄露信息。
TAI64N_OFFSET = 0x400000000000000A
NSEC_ALIGN = 2 ** 24


def tai64n_now(unix_seconds: float | None = None) -> bytes:
    ts = time.time() if unix_seconds is None else unix_seconds
    sec = int(ts)
    nsec = int(round((ts - sec) * 1e9))
    nsec -= nsec % NSEC_ALIGN
    return struct.pack(">QI", TAI64N_OFFSET + sec, nsec)


def tai64n_newer(a: bytes, b: bytes) -> bool:
    """定长大端字段逐字节比较即时间先后（对应内核 memcmp 判据）。"""
    return a > b


def _self_test() -> int:
    checks = 0
    assert blake2s(b"abc").hex() == \
        "508c5e8c327c14e2e1a72ba34eeb452f37458b209ed63a294d999b4c86675982"
    checks += 1
    # keyed 模式与未键控模式的输出必须不同（否则 MAC1 就等于普通摘要）
    assert blake2s(b"m", 16, b"k") != blake2s(b"m", 16)
    assert len(blake2s(b"m", 16, b"k")) == 16
    checks += 2
    # 与标准库 hmac 模块互证（同哈希、同块长）
    for k, m in ((b"key", b"msg"), (bytes(range(100)), b"m"), (b"", b"x")):
        assert hmac_blake2s(k, m) == _hmac.new(
            k, m, lambda d=b"": hashlib.blake2s(d, digest_size=32)).digest()
    checks += 3

    ck = blake2s(b"ck")
    sec = hmac_blake2s(ck, b"ikm")
    k1, k2 = kdf(ck, b"ikm", 32, 32)
    assert k1 == hmac_blake2s(sec, b"\x01")
    assert k2 == hmac_blake2s(sec, k1 + b"\x02")
    checks += 2
    c2, h2, k3 = kdf(ck, b"ikm", 32, 32, 32)
    assert c2 == k1 and k3 == hmac_blake2s(sec, h2 + b"\x03")
    assert h2 == hmac_blake2s(sec, c2 + b"\x02")
    checks += 3
    assert kdf(ck, b"x", 32)[0] == hmac_blake2s(hmac_blake2s(ck, b"x"), b"\x01")
    assert len(kdf(ck, b"", 32, 32)[0]) == 32        # Split 的 data_len=0
    checks += 2

    ts = tai64n_now(1_700_000_000.123456789)
    assert len(ts) == 12
    assert struct.unpack(">Q", ts[:8])[0] == TAI64N_OFFSET + 1_700_000_000
    nsec = struct.unpack(">I", ts[8:])[0]
    assert nsec % NSEC_ALIGN == 0 and 0 <= nsec <= 123_456_789
    # 同一秒内两次取样可能给出相同时间戳（对齐到 16.78 ms），但绝不倒退
    assert tai64n_newer(tai64n_now(2_000_000_000), tai64n_now(1_000_000_000))
    assert not tai64n_newer(tai64n_now(1_000_000_000), tai64n_now(2_000_000_000))
    checks += 4

    print(f"wg_kdf: {checks} checks passed")
    return checks


if __name__ == "__main__":
    _self_test()
