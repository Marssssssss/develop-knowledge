"""教科书式 RSA-FDH 签名与 base64url 工具（DPoP demo 的底层依赖）。

RFC 9449 §4.3(5) 要求 alg 必须是已注册的非对称算法（ES256 / EdDSA 等）。
本机没有第三方密码库，为了让"签名—验签"真实可跑，这里内置一个 256-bit
素数的教科书 RSA：`sig = H(m)^d mod n`，验签为 `sig^e mod n == H(m)`。
它**不提供生产强度**，只承担"持有私钥才能产出、公钥可验"这一语义。
从 dpop.py 拆出以满足单文件 ≤300 行的约束。
"""

import base64
import hashlib
import json
import random

# ---------------------------------------------------------------- base64url
def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64u_dec(s):
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sha256_bytes(data):
    return hashlib.sha256(data).digest()


# ------------------------------------------------------- 教科书 RSA-FDH 签名
def _is_probable_prime(n, rounds=24):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = random.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits):
    while True:
        p = random.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(p):
            return p


class KeyPair:
    """教科书 RSA 密钥对。仅用于演示签名/验签语义，非生产强度。"""

    E = 65537

    def __init__(self, bits=256, seed=None):
        rng = random.Random(seed)
        old = random.getstate()
        random.setstate(rng.getstate())
        try:
            while True:
                p = _gen_prime(bits)
                q = _gen_prime(bits)
                if p == q:
                    continue
                n = p * q
                phi = (p - 1) * (q - 1)
                if phi % self.E == 0:
                    continue
                self.n = n
                self.e = self.E
                self.d = pow(self.E, -1, phi)
                return
        finally:
            random.setstate(old)

    def sign_digest_int(self, h):
        return pow(h, self.d, self.n)

    def recover(self, sig):
        return pow(sig, self.e, self.n)


def _int_to_be(x):
    length = max(1, (x.bit_length() + 7) // 8)
    return x.to_bytes(length, "big")


def jwk_public(kp):
    """RFC 7517 公钥表示（RSA 只需 e 与 n，均为 base64url 无填充）。"""
    return {"kty": "RSA", "e": b64u(_int_to_be(kp.e)), "n": b64u(_int_to_be(kp.n))}


def jwk_thumbprint(jwk):
    """RFC 7638 §3.2：按成员名**字典序**、无空白、无填充的 JSON 的 SHA-256 base64url。"""
    canonical = json.dumps(jwk, separators=(",", ":"), sort_keys=True,
                           ensure_ascii=False).encode("utf-8")
    return b64u(sha256_bytes(canonical))
