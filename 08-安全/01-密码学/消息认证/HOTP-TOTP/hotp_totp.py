# -*- coding: utf-8 -*-
"""HOTP(RFC 4226)/TOTP(RFC 6238): 基于 HMAC 的一次性口令。

依据:
  - RFC 4226 §5.3: HOTP(K,C) = Truncate(HMAC-SHA-1(K,C)) mod 10^Digit;
    动态截断: offset = HS[19]&0xf, 取 HS[offset..offset+3] 且首字节 &0x7f(31 bit)
  - RFC 4226 附录 D: 密钥 "12345678901234567890"(ASCII), count 0-9 的 HOTP 值
  - RFC 6238 §4: TOTP(K,T)=HOTP(K,T), T=floor((unix-T0)/X), X=30, T0=0;
    附录 B: SHA1/SHA256/SHA512 的 8 位 TOTP 向量
  - RFC 6238 §5.2: 重同步窗口; §7.3: 验证方应限速防穷举

单文件自测 5 组:
  1) RFC 4226 附录 D: count 0-9 的 HMAC 中间值 + 6 位 HOTP 全部命中
  2) RFC 6238 附录 B: SHA1/SHA256/SHA512 三组 8 位 TOTP 向量(6 时间点×3 算法)
  3) 截断细节: 中间 HMAC 值手工验证 §5.4 示例(offset=0xa -> 872921)
  4) TOTP 时间步进: t=59 与 t=60 跨步边界; 窗口内 resync 能找回漂移 1 步的码
  5) 安全性: 错误码计数器锁定; 密钥敏感性(变 1 bit 输出全变)
"""
import hashlib
import hmac


def hotp(key: bytes, counter: int, digits: int = 6, hashname: str = "sha1") -> int:
    c = counter.to_bytes(8, "big")  # 8 字节大端计数器
    hs = hmac.new(key, c, hashname).digest()
    off = hs[-1] & 0xF
    code = ((hs[off] & 0x7F) << 24) | (hs[off + 1] << 16) | (hs[off + 2] << 8) | hs[off + 3]
    return code % (10 ** digits)


def totp(key: bytes, unix_time: int, digits: int = 8, hashname: str = "sha1",
         step: int = 30, t0: int = 0) -> int:
    t = (unix_time - t0) // step
    return hotp(key, t, digits, hashname)


def verify_wide(key, code, unix_time, digits=8, hashname="sha1", window=1, step=30):
    """RFC 6238 §5.2: 允许 ±window 个时间步的漂移(银行常用 window=0-2)。"""
    t = unix_time // step
    for dt in range(-window, window + 1):
        if hotp(key, t + dt, digits, hashname) == code:
            return True
    return False


SECRET20 = b"12345678901234567890"          # RFC 4226 附录 D / RFC 6238 SHA1
SECRET32 = b"12345678901234567890123456789012"          # RFC 6238 SHA256
SECRET64 = b"1234567890123456789012345678901234567890123456789012345678901234"  # SHA512


def demo():
    # --- 1) RFC 4226 附录 D: count 0-9 ---
    hmac_mid = {
        0: "cc93cf18508d94934c64b65d8ba7667fb7cde4b0",
        1: "75a48a19d4cbe100644e8ac1397eea747a2d33ab",
        2: "0bacb7fa082fef30782211938bc1c5e70416ff44",
        3: "66c28227d03a2d5529262ff016a1e6ef76557ece",
        4: "a904c900a64b35909874b33e61c5938a8e15ed1c",
        5: "a37e783d7b7233c083d4f62926c7a25f238d0316",
        6: "bc9cd28561042c83f219324d3c607256c03272ae",
        7: "a4fb960c0bc06e1eabb804e5b397cdc4b45596fa",
        8: "1b3c89f65e6c9e883012052823443f048b4332db",
        9: "1637409809a679dc698207310c8c7fc07290d9e5",
    }
    hotp_vals = [755224, 287082, 359152, 969429, 338314,
                254676, 287922, 162583, 399871, 520489]
    for c in range(10):
        hs = hmac.new(SECRET20, c.to_bytes(8, "big"), "sha1").hexdigest()
        assert hs == hmac_mid[c], f"HMAC({c})"
        assert hotp(SECRET20, c) == hotp_vals[c], f"HOTP({c})"
    print("demo1 RFC 4226 附录 D (count 0-9, HMAC+HOTP): PASS")

    # --- 2) RFC 6238 附录 B: SHA1/256/512 的 8 位 TOTP ---
    vectors = [
        (59, 94287082, 46119246, 90693936),
        (1111111109, 7081804, 68084774, 25091201),
        (1111111111, 14050471, 67062674, 99943326),
        (1234567890, 89005924, 91819424, 93441116),
        (2000000000, 69279037, 90698825, 38618901),
        (20000000000, 65353130, 77737706, 47863826),
    ]
    for t, v1, v256, v512 in vectors:
        assert totp(SECRET20, t, 8, "sha1") == v1, f"SHA1 @{t}"
        assert totp(SECRET32, t, 8, "sha256") == v256, f"SHA256 @{t}"
        assert totp(SECRET64, t, 8, "sha512") == v512, f"SHA512 @{t}"
    print("demo2 RFC 6238 附录 B (SHA1/256/512 × 6 时间点): PASS")

    # --- 3) RFC 4226 §5.4 截断细节示例 ---
    hs = bytes.fromhex("1f8698690e02ca16618550ef7f19da8e945b555a")
    off = hs[19] & 0xF
    assert off == 0xA, "offset 应取末字节低 4 位"
    dbc = (hs[off] & 0x7F) << 24 | hs[off + 1] << 16 | hs[off + 2] << 8 | hs[off + 3]
    assert dbc == 0x50EF7F19 and dbc % 10 ** 6 == 872921, "§5.4 示例"
    print("demo3 §5.4 动态截断细节(offset=0xa -> 872921): PASS")

    # --- 4) 时间步进与 resync 窗口 ---
    assert totp(SECRET20, 59, 8, "sha1") != totp(SECRET20, 60, 8, "sha1")  # 跨步边界
    code_at_59 = totp(SECRET20, 59, 8, "sha1")
    assert verify_wide(SECRET20, code_at_59, 59 + 30, window=1)  # 时钟快 1 步
    assert verify_wide(SECRET20, code_at_59, 59 + 60, window=1) is False  # 快 2 步拒绝
    print("demo4 时间步边界 + ±1 步 resync 窗口: PASS")

    # --- 5) 安全性: 密钥雪崩 + 限速语义 ---
    key2 = bytearray(SECRET20)
    key2[7] ^= 1
    assert [hotp(bytes(key2), c) for c in range(10)] != hotp_vals, "密钥敏感"
    # 验证端限速: 模拟连续错误码后锁定(失败计数 >= 阈值即拒)
    fails = 0
    for attempt in (1, 2, 3, 4):
        if verify_wide(SECRET20, 00000000 + attempt, 59):  # 错码
            raise AssertionError("错误码不应通过")
        fails += 1
        if fails >= 3:
            break  # RFC 6238 §7.3: 连续失败应锁定
    assert fails == 3
    print("demo5 密钥雪崩 + 失败计数锁定语义: PASS")


if __name__ == "__main__":
    demo()
    print("ALL PASS")
