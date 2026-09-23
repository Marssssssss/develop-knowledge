"""SPAKE2（RFC 9382），P256-SHA256-HKDF-HMAC 套件。

协议（§3.3）：
    A: X = x*P,  pA = w*M + X
    B: Y = y*P,  pB = w*N + Y
    A: K = h*x*(pB - w*N)
    B: K = h*y*(pA - w*M)

    TT = len(A)||A || len(B)||B || len(pA)||pA || len(pB)||pB
      || len(K)||K || len(w)||w                       # len 是 8 字节小端

密钥调度（§4）：
    Ke || Ka        = Hash(TT)
    KcA || KcB      = KDF(Ka, nil, "ConfirmationKeys" || AAD, L)
    cA = MAC(KcA, TT),  cB = MAC(KcB, TT)
"""

import hashlib
import hmac

from p256 import (P, N, A as CURVE_A, B as CURVE_B, G, H,
                  FIELD_LEN, mul, add, neg, encode_uncompressed, decode)
from spake2_vectors import P256_M, P256_N

M = decode(bytes.fromhex(P256_M))
N_POINT = decode(bytes.fromhex(P256_N))

HASH_LEN = 32          # SHA-256
CONFIRM_INFO = b"ConfirmationKeys"


def hkdf_extract(salt, ikm):
    """RFC 5869 §2.2；salt 为 None 时按规范取 HashLen 个零字节。"""
    if salt is None:
        salt = b"\x00" * HASH_LEN
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk, info, length):
    """RFC 5869 §2.3。"""
    if length > 255 * HASH_LEN:
        raise ValueError("HKDF-Expand output too long")
    out = b""
    t = b""
    counter = 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        out += t
        counter += 1
    return out[:length]


def kdf(ikm, salt, info, length):
    """RFC 9382 §3.2 的 KDF(ikm, salt, info, L)，此处即 HKDF。"""
    return hkdf_expand(hkdf_extract(salt, ikm), info, length)


def mac(key, msg):
    return hmac.new(key, msg, hashlib.sha256).digest()


def hash_tt(tt):
    return hashlib.sha256(tt).digest()


def len_prefix(s):
    """len(S) 是 8 字节**小端**（RFC 9382 §3.2）。"""
    return len(s).to_bytes(8, "little")


def encode_w(w):
    """w 编码为大端、左侧补零到 p 的字节长度——与 w 的实际大小无关。"""
    return w.to_bytes(FIELD_LEN, "big")


def build_tt(ident_a, ident_b, pA, pB, K, w):
    """RFC 9382 §3.3 的 transcript。身份可以是 str 或 bytes。"""
    a = ident_a.encode() if isinstance(ident_a, str) else ident_a
    b = ident_b.encode() if isinstance(ident_b, str) else ident_b
    return b"".join([
        len_prefix(a), a,
        len_prefix(b), b,
        len_prefix(pA), pA,
        len_prefix(pB), pB,
        len_prefix(K), K,
        len_prefix(encode_w(w)), encode_w(w),
    ])


# ------------------------------------------------------------------ 协议

class Party:
    """一方（A 或 B）。role 决定用 M 还是 N、以及自己的确认密钥是 KcA 还是 KcB。"""

    def __init__(self, role, ident_self, ident_peer, w, rand):
        self.role = role                      # "A" 或 "B"
        self.ident_self = ident_self
        self.ident_peer = ident_peer
        self.w = w % N
        self.rand = rand % N
        self.pub = None                       # pA / pB 的字节串
        self.K = None
        self.Ke = None

    def start(self):
        """§3.3 第一步：X = x*P，pA = w*M + X（B 侧用 N）。"""
        blind = M if self.role == "A" else N_POINT
        X = mul(self.rand, G)
        self.pub = encode_uncompressed(add(mul(self.w, blind), X))
        return self.pub

    def finish(self, peer_pub, aad=b""):
        """§3.3 第二步：算出 K，再走 §4 的密钥调度。"""
        blind = N_POINT if self.role == "A" else M
        peer = decode(peer_pub)
        inner = add(peer, neg(mul(self.w, blind)))       # pB - w*N  /  pA - w*M
        kpt = mul(self.rand, inner)
        self.K = encode_uncompressed(mul(H, kpt))          # 乘余因子 h

        if self.role == "A":
            tt = build_tt(self.ident_self, self.ident_peer,
                          self.pub, peer_pub, self.K, self.w)
        else:
            tt = build_tt(self.ident_peer, self.ident_self,
                          peer_pub, self.pub, self.K, self.w)
        self.tt = tt
        digest = hash_tt(tt)
        self.Ke, self.Ka = digest[:16], digest[16:]
        kc = kdf(self.Ka, b"", CONFIRM_INFO + aad, 32)
        self.Kc_self, self.Kc_peer = (kc[:16], kc[16:]) if self.role == "A" \
            else (kc[16:], kc[:16])
        self.confirm = mac(self.Kc_self, tt)
        return self.Ke, self.confirm

    def verify(self, peer_confirm):
        return hmac.compare_digest(self.confirm_expected(), peer_confirm)

    def confirm_expected(self):
        return mac(self.Kc_peer, self.tt)


def run(ident_a, ident_b, w, x, y, aad=b""):
    """完整跑一轮，返回 (Ke_A, Ke_B, cA, cB, tt_ok)。"""
    a = Party("A", ident_a, ident_b, w, x)
    b = Party("B", ident_b, ident_a, w, y)
    pA = a.start()
    pB = b.start()
    ke_a, cA = a.finish(pB, aad)
    ke_b, cB = b.finish(pA, aad)
    return ke_a, ke_b, cA, cB, a.verify(cB) and b.verify(cA)


def impersonate_guess(ident_a, ident_b, pA, y, w_guess, expect_cA, aad=b""):
    """离线字典攻击：冒充 B 拿到 pA 与 A 的确认消息后，可离线枚举 w。

    对候选 w' 重放 B 侧计算：pB = w'*N + Y，K = y*(pA - w'*M)，
    再走密钥调度算出 KcA，检查 MAC(KcA, TT) 是否等于收到的 cA。
    猜中就能离线确认——这正是 PAKE 只能用「每轮一次猜测」来刻画安全性的原因。
    """
    b = Party("B", ident_b, ident_a, w_guess, y)
    b.start()                       # pB = w'*N + Y，用的是**猜测**的 w'
    b.finish(pA, aad)
    return hmac.compare_digest(b.confirm_expected(), expect_cA)
