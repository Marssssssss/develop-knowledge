# AES-GCM (Galois/Counter Mode) — NIST SP 800-38D (无第三方依赖,纯 Python stdlib)
#
# 参考:
#   NIST SP 800-38D, "Recommendation for Block Cipher Modes of Operation:
#   Galois/Counter Mode (GCM) and GMAC", November 2007
#   https://nvlpubs.nist.gov/nistpubs/legacy/sp/nistspecialpublication800-38d.pdf
#
# 实现 §6.4 GHASH、§6.5 GCTR、§7.1 GCM-AE_Encrypt_K、§7.2 GCM-AE_Decrypt_K。
# 仅支持 96 位(12 字节)IV(NIST §8.2.1 推荐的 deterministic construction)。

from aes128 import Aes128  # 允许 `python main.py` 直接运行


def _ghash_mul(x: int, y: int) -> int:
    """NIST §6.3 在 GF(2^128) 上的乘法,约简多项式 x^128 + x^7 + x^2 + x + 1(0xe1<<120)。

    这里 x, y 都是 128 位整数,小端 bit 顺序(即 [0] 是最低有效位)。
    """
    R = 0xe1 << 120  # 约简多项式
    z = 0
    v = y
    for _ in range(128):
        if x & 1:
            z ^= v
        x >>= 1
        if v & 1:
            v = (v >> 1) ^ R
        else:
            v >>= 1
    return z


def _ghash(h: int, aad: bytes, ct: bytes) -> int:
    """NIST §6.4 GHASH_H(A || C)。返回 128 位整数。

    A 和 C 都按 16 字节切块,最后一块零填充;再拼接 A||C 的 64 位位长;
    对每块做 H 乘法迭代累加。
    """
    # AAD 切块
    a_blocks = [aad[i:i + 16] for i in range(0, max(len(aad), 16), 16)]
    if len(aad) == 0:
        a_blocks = [b"\x00" * 16]
    elif len(aad) % 16 != 0:
        # 末尾补零直到 16 字节
        last = a_blocks[-1] + b"\x00" * (16 - len(a_blocks[-1]))
        a_blocks[-1] = last
    c_blocks = [ct[i:i + 16] for i in range(0, max(len(ct), 16), 16)]
    if len(ct) == 0:
        c_blocks = [b"\x00" * 16]
    elif len(ct) % 16 != 0:
        last = c_blocks[-1] + b"\x00" * (16 - len(c_blocks[-1]))
        c_blocks[-1] = last
    y = 0
    for blk in a_blocks + c_blocks:
        x = int.from_bytes(blk, "big")
        y = _ghash_mul(y ^ x, h)
    # 末尾追加长度块:len(A)*8 (64 bits) || len(C)*8 (64 bits)
    len_block = (len(aad) * 8).to_bytes(8, "big") + (len(ct) * 8).to_bytes(8, "big")
    y = _ghash_mul(y ^ int.from_bytes(len_block, "big"), h)
    return y


def _inc32(j0: int) -> int:
    """NIST §6.2 inc_32:把 J0 的最高 32 位视为无符号整数 +1,低 96 位不变。"""
    high = (j0 >> 96) & 0xffffffff
    low = j0 & ((1 << 96) - 1)
    return ((high + 1) & 0xffffffff) << 96 | low


def _gctr(cipher: Aes128, icb: int, data: bytes) -> bytes:
    """NIST §6.5 GCTR_K(ICB, X),CTR 模式加密/解密(XOR 流密码)。"""
    out = bytearray()
    if not data:
        return bytes(out)
    n = (len(data) + 15) // 16  # 块数
    cb = icb
    for i in range(n - 1):
        ks = cipher.encrypt_block(cb.to_bytes(16, "big"))
        blk = data[i * 16:(i + 1) * 16]
        out += bytes(a ^ b for a, b in zip(ks, blk))
        cb = _inc32(cb)
    # 最后一块:可能 <16 字节,密钥流截断
    ks = cipher.encrypt_block(cb.to_bytes(16, "big"))
    last = data[(n - 1) * 16:]
    out += bytes(a ^ b for a, b in zip(ks[:len(last)], last))
    return bytes(out)


class AesGcm:
    """AES-GCM 认证加密(NIST SP 800-38D),96 位 IV。

    用法:
        gcm = AesGcm(key_16_bytes)
        ct, tag = gcm.encrypt(iv_12_bytes, plaintext, aad)
        pt = gcm.decrypt(iv_12_bytes, ct, aad, tag)  # 失败抛 ValueError
    """

    TAG_LEN = 16  # 默认 128 位 tag(NIST 推荐)

    def __init__(self, key: bytes):
        self._cipher = Aes128(key)
        # H = E_K(0^128),NIST §6.4 定义
        h_bytes = self._cipher.encrypt_block(b"\x00" * 16)
        self._h = int.from_bytes(h_bytes, "big")

    def encrypt(self, iv: bytes, plaintext: bytes, aad: bytes = b"") -> tuple:
        """NIST §7.1 GCM-AE_K(IV, P, A) = (C, T)。

        IV 必须是 12 字节;tag 16 字节。
        """
        assert len(iv) == 12, "GCM 仅支持 12 字节(96 位)IV"
        # J0 = IV || 0^31 || 1,即 IV 后跟 4 字节 0x00000001
        j0 = int.from_bytes(iv + b"\x00\x00\x00\x01", "big")
        # 计数器从 J0 + 1 开始(NIST §7.1 step 3)
        ct = _gctr(self._cipher, _inc32(j0), plaintext)
        # 计算 S = GHASH_H(A || C || len(A)*8 || len(C)*8),NIST §7.1 step 4
        s = _ghash(self._h, aad, ct)
        # T = MSB_t(GCTR_K(J0, S)),NIST §7.1 step 5
        tag_full = _gctr(self._cipher, j0, s.to_bytes(16, "big"))
        return ct, tag_full[:self.TAG_LEN]

    def decrypt(self, iv: bytes, ciphertext: bytes, aad: bytes, tag: bytes) -> bytes:
        """NIST §7.2 GCM-AD_K(IV, C, A, T) = P 或 FAIL。tag 不匹配抛 ValueError。"""
        assert len(iv) == 12
        assert len(tag) >= self.TAG_LEN
        j0 = int.from_bytes(iv + b"\x00\x00\x00\x01", "big")
        s = _ghash(self._h, aad, ciphertext)
        expected_tag = _gctr(self._cipher, j0, s.to_bytes(16, "big"))[:self.TAG_LEN]
        # 常数时间比较,避免时序攻击
        diff = 0
        for a, b in zip(expected_tag, tag[:self.TAG_LEN]):
            diff |= a ^ b
        if diff != 0:
            raise ValueError("GCM tag 校验失败(密文或 AAD 被篡改)")
        return _gctr(self._cipher, _inc32(j0), ciphertext)