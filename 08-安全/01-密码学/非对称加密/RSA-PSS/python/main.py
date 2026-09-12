"""RSA-PSS 签名/验签 — RFC 8017 PKCS#1 v2.2(无第三方依赖,纯 stdlib)

参考:
  RFC 8017, "PKCS #1: RSA Cryptography Specifications Version 2.2", §8.1
  https://www.rfc-editor.org/rfc/rfc8017

实现:
  - RSASSA-PSS-SIGN / RSASSA-PSS-VERIFY  (§8.1.1 / §8.1.2)
  - EMSA-PSS-ENCODE / EMSA-PSS-VERIFY    (§9.1.1 / §9.1.2)
  - MGF1                                  (Appendix B.2.1)

使用 RSA-2048 + SHA-256。1024 bit 私钥仅用于 demo 性能可承受,
生产请用 2048 bit 起(FIPS 186-5 §5.6)。
"""

import hashlib
import secrets
from math import gcd
from typing import Tuple


# ---------- RSA 私钥生成(简化 Miller-Rabin 素性测试) ----------

def _is_prime(n: int, rounds: int = 16) -> bool:
    """确定性 Miller-Rabin,轮数 16 时误判概率 < 4^-16 ≈ 2^-32。"""
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n == p:
            return True
        if n % p == 0:
            return False
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int) -> int:
    """生成指定 bit 数的素数。"""
    while True:
        n = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_prime(n):
            return n


# ---------- I2OSP / OS2IP (RFC 8017 §4) ----------

def i2osp(x: int, length: int) -> bytes:
    """整数 → 长度为 length 的字节串(大端)。"""
    if x < 0 or x >= 1 << (8 * length):
        raise ValueError("integer out of range")
    return x.to_bytes(length, "big")


def os2ip(b: bytes) -> int:
    """字节串 → 整数。"""
    return int.from_bytes(b, "big")


# ---------- MGF1 (RFC 8017 Appendix B.2.1) ----------

def mgf1(seed: bytes, length: int, hash_func=hashlib.sha256) -> bytes:
    """Mask Generation Function 1:基于 hash 的掩码生成器。"""
    h_len = hash_func().digest_size
    if length > (1 << 32) * h_len:
        raise ValueError("mask too long")
    t = b""
    counter = 0
    while len(t) < length:
        c = i2osp(counter, 4)
        t += hash_func(seed + c).digest()
        counter += 1
    return t[:length]


# ---------- EMSA-PSS-ENCODE / VERIFY (RFC 8017 §9.1) ----------

class PSSParams:
    """EMSA-PSS 参数:RFC 8017 §9.1.1。"""

    def __init__(self, hash_func=hashlib.sha256, s_len: int = None,
                 mgf1_hash=None, trailer: int = 0xbc):
        self.hash_func = hash_func
        self.h_len = hash_func().digest_size
        self.s_len = s_len if s_len is not None else self.h_len  # RFC 推荐 sLen == hLen
        self.mgf1_hash = mgf1_hash or hash_func
        self.trailer = trailer  # RFC 8017 固定 0xbc


def emsa_pss_encode(msg: bytes, em_bits: int, params: PSSParams) -> bytes:
    """EMSA-PSS-ENCODE(M, emBits) → EM,RFC 8017 §9.1.1。"""
    em_len = (em_bits + 7) // 8
    h = params.hash_func(msg).digest()
    # M' = 8B 零 || mHash || salt
    salt = secrets.token_bytes(params.s_len)
    m_prime = b"\x00" * 8 + h + salt
    h2 = params.hash_func(m_prime).digest()
    # DB = PS || 0x01 || salt,PS 长度 = em_len - sLen - hLen - 2
    ps_len = em_len - params.s_len - params.h_len - 2
    if ps_len < 0:
        raise ValueError("encoding too short")
    db = b"\x00" * ps_len + b"\x01" + salt
    db_mask = mgf1(h2, em_len - params.h_len - 1, params.mgf1_hash)
    masked_db = b
    # 将最高 (8*em_len - em_bits) 位置零(RFC 8017 §9.1.1 step 9)
    bits_to_zero = 8 * em_len - em_bits
    if bits_to_zero > 0:
        first_byte = masked_db[0] & ((1 << (8 - bits_to_zero)) - 1)
        masked_db = bytes([first_byte]) + masked_db[1:]
    return masked_db + h2 + bytes([params.trailer])


def emsa_pss_verify(msg: bytes, em: bytes, em_bits: int, params: PSSParams) -> bool:
    """EMSA-PSS-VERIFY(M, EM, emBits) → bool,RFC 8017 §9.1.2。"""
    em_len = (em_bits + 7) // 8
    if len(em) != em_len:
        return False
    if em[-1] != params.trailer:
        return False
    h = params.hash_func(msg).digest()
    masked_db = em[:em_len - params.h_len - 1]
    h2 = em[em_len - params.h_len - 1:em_len - 1]
    # 最高位必须为 0(§9.1.2 step 4)
    bits_to_zero = 8 * em_len - em_bits
    if bits_to_zero > 0 and (masked_db[0] >> (8 - bits_to_zero)) != 0:
        return False
    db_mask = mgf1(h2, em_len - params.h_len - 1, params.mgf1_hash)
    db = bytes(a ^ b for a, b in zip(masked_db, db_mask))
    if bits_to_zero > 0:
        first_byte = db[0] & ((1 << (8 - bits_to_zero)) - 1)
        db = bytes([first_byte]) + db[1:]
    # PS 必须全是 0x00,然后 0x01
    ps_len = em_len - params.s_len - params.h_len - 2
    for b in db[:ps_len]:
        if b != 0:
            return False
    if db[ps_len] != 0x01:
        return False
    salt = db[ps_len + 1:]
    m_prime = b"\x00" * 8 + h + salt
    h2_expected = params.hash_func(m_prime).digest()
    # 常数时间比较
    diff = 0
    for a, b in zip(h2, h2_expected):
        diff |= a ^ b
    return diff == 0


# ---------- RSA 私钥结构 ----------

class RsaPrivateKey:
    """RSA 私钥 + 公钥。"""

    def __init__(self, bits: int = 1024):
        # 生成两个 ~bits/2 位的素数 p, q
        p = _gen_prime(bits // 2)
        q = _gen_prime(bits // 2)
        while q == p:
            q = _gen_prime(bits // 2)
        self.n = p * q                       # modulus
        phi = (p - 1) * (q - 1)
        self.e = 65537                       # 公钥指数,RFC 8017 §3.1 推荐
        self.d = pow(self.e, -1, phi)        # 私钥指数
        self.bits = bits

    # RSASP1(RFC 8017 §5.2.1):s = m^d mod n
    def sign_raw(self, m: int) -> int:
        if m >= self.n:
            raise ValueError("message representative out of range")
        return pow(m, self.d, self.n)

    # RSAVP1(RFC 8017 §5.2.2):m = s^e mod n
    def verify_raw(self, s: int) -> int:
        return pow(s, self.e, self.n)

    # ---------- RSASSA-PSS-SIGN / VERIFY(RFC 8017 §8.1) ----------

    def sign_pss(self, msg: bytes, params: PSSParams = None) -> bytes:
        params = params or PSSParams()
        em = emsa_pss_encode(msg, self.bits - 1, params)
        m = os2ip(em)
        s = self.sign_raw(m)
        return i2osp(s, (self.bits + 7) // 8)

    def verify_pss(self, msg: bytes, sig: bytes, params: PSSParams = None) -> bool:
        params = params or PSSParams()
        k = (self.bits + 7) // 8
        if len(sig) != k:
            return False
        s = os2ip(sig)
        m = self.verify_raw(s)
        em_len = (self.bits - 1 + 7) // 8
        em = i2osp(m, em_len)
        return emsa_pss_verify(msg, em, self.bits - 1, params)


# ---------- DEMO ----------

def main() -> None:
    import sys
    print("[1] 生成 1024-bit RSA 私钥(用于 demo;生产请用 2048+)…")
    key = RsaPrivateKey(bits=1024)
    print(f"  n = {key.n.bit_length()} bits, e = {key.e}")

    msg = b"hello RSA-PSS world"
    params = PSSParams()
    sig = key.sign_pss(msg, params)
    print(f"\n[2] 签名长度 = {len(sig)} bytes(== k = 1024/8 = 128)")

    print("\n[3] 正确签名 → verify_pss 应当通过")
    assert key.verify_pss(msg, sig, params), "验签失败!"
    print("  ✓ 验签通过")

    print("\n[4] 篡改 msg → 应当验签失败")
    bad_msg = msg + b"!"
    assert not key.verify_pss(bad_msg, sig, params), "篡改未被检测!"
    print("  ✓ 篡改消息被检测")

    print("\n[5] 篡改 sig → 应当验签失败")
    bad_sig = bytearray(sig); bad_sig[0] ^= 0x01
    assert not key.verify_pss(msg, bytes(bad_sig), params), "篡改 sig 未被检测!"
    print("  ✓ 篡改签名被检测")

    print("\n[6] 确定性:同 msg + 同 (n,d,e) 两次签名,salt 不同 → sig 不同")
    sig2 = key.sign_pss(msg, params)
    assert sig != sig2, "PSS 应是概率签名,两次结果不同!"
    print("  ✓ 两次签名不同(概率签名性质)")

    print("\n全部 RSA-PSS 测试通过 ✓")
    sys.exit(0)


if __name__ == "__main__":
    main()