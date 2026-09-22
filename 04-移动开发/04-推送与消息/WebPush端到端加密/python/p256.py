"""P-256 素域椭圆曲线与 ECDSA 的最小可验证实现。

只用于给 APNs provider token 做 ES256 签名（RFC 7518 §3.4），因此只实现
签名与验签，不做点压缩/ASN.1 编码。

曲线参数取自 FIPS 186-4（P-256 / prime256v1）；确定性 nonce 的生成步骤
严格照抄 RFC 6979 §2.3.2 ~ §2.3.4 与 §3.2：
    bits2int  —— 长于 qlen 时**截左**，不是截右
    bits2octets —— z1 = bits2int(b) 之后**模 q**，不是直接 int2octets
    int2octets  —— rlen = 8*ceil(qlen/8) 字节序
"""
import hashlib
import hmac

P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
A = P - 3
B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
GX = 0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296
GY = 0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
G = (GX, GY)
QLEN = 256
RLEN = 32


def inv(x, m=P):
    """扩展欧几里得求逆；m 为素数时 x 非 0 即存在逆元。"""
    x %= m
    if x == 0:
        raise ZeroDivisionError('no inverse for 0')
    old_r, r = x, m
    old_s, s = 1, 0
    while r:
        q = old_r // r
        old_r, r = r, old_r - q * r
        old_s, s = s, old_s - q * s
    return old_s % m


def is_on_curve(pt):
    x, y = pt
    if not (0 <= x < P and 0 <= y < P):
        return False
    return (y * y - (x * x * x + A * x + B)) % P == 0


def add(p1, p2):
    """点加。p1 或 p2 为 None 表示无穷远点 O。"""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + A) * inv(2 * y1) % P
    else:
        lam = (y2 - y1) * inv(x2 - x1) % P
    x3 = (lam * lam - x1 - x2) % P
    return (x3, (lam * (x1 - x3) - y1) % P)


def mul(k, pt=G):
    """标量乘。k 为 0 返回 O；k 不在这里做 mod N，调用方自己负责规约。"""
    if k % P == 0 or pt is None:
        return None
    if k < 0:
        k = -k
        pt = (pt[0], (-pt[1]) % P)
    r = None
    while k:
        if k & 1:
            r = add(r, pt)
        pt = add(pt, pt)
        k >>= 1
    return r


def public_key(d):
    """由私钥标量 d 得公钥点 U = dG。"""
    if not (1 <= d < N):
        raise ValueError('private key out of range')
    return mul(d, G)


def point_to_bytes(pt):
    """X9.62 未压缩点形式：0x04 || X || Y，共 65 字节。"""
    x, y = pt
    return b'\x04' + x.to_bytes(32, 'big') + y.to_bytes(32, 'big')


def bytes_to_point(raw):
    if len(raw) != 65 or raw[0] != 0x04:
        raise ValueError('expect 65-octet uncompressed point')
    pt = (int.from_bytes(raw[1:33], 'big'), int.from_bytes(raw[33:65], 'big'))
    if not is_on_curve(pt):
        raise ValueError('point not on curve')
    return pt


def bits2int(b):
    """RFC 6979 §2.3.2：qlen < blen 时保留**左起** qlen 位。"""
    z = int.from_bytes(b, 'big')
    blen = len(b) * 8
    if blen > QLEN:
        z >>= (blen - QLEN)
    return z


def int2octets(x):
    """RFC 6979 §2.3.3：rlen 位大端。"""
    return x.to_bytes(RLEN, 'big')


def bits2octets(b):
    """RFC 6979 §2.3.4：bits2int 之后**再模 q** —— 不是直接截断。"""
    return int2octets(bits2int(b) % N)


def _hmac(key, *parts):
    h = hmac.new(key, b''.join(parts), hashlib.sha256)
    return h.digest()


def _candidate_nonces(d, h1):
    """RFC 6979 §3.2 的 k 生成器；每次 yield 一个候选 k。"""
    qlen = N.bit_length()
    rlen = (qlen + 7) // 8
    v = b'\x01' * 32
    k = b'\x00' * 32
    priv = int2octets(d)
    hb = bits2octets(h1)
    k = _hmac(k, v, b'\x00', priv, hb)
    v = _hmac(k, v)
    k = _hmac(k, v, b'\x01', priv, hb)
    v = _hmac(k, v)
    while True:
        t = b''
        while len(t) * 8 < qlen:
            v = _hmac(k, v)
            t += v
        k_cand = bits2int(t)
        if 1 <= k_cand < N:
            yield k_cand
        k = _hmac(k, v, b'\x00')
        v = _hmac(k, v)
        assert rlen == RLEN


def sign(d, msg, hasher=hashlib.sha256):
    """ECDSA 签名，返回 (r, s)。nonce 由 RFC 6979 确定性生成。"""
    h1 = hasher(msg).digest()
    h = bits2int(h1) % N
    for k in _candidate_nonces(d, h1):
        r_pt = mul(k, G)
        r = r_pt[0] % N
        if r == 0:
            continue
        s = inv(k, N) * (h + r * d) % N
        if s == 0:
            continue
        return (r, s)
    raise RuntimeError('unreachable')


def verify(pub, msg, sig, hasher=hashlib.sha256):
    """ECDSA 验签。"""
    r, s = sig
    if not (1 <= r < N and 1 <= s < N):
        return False
    h = bits2int(hasher(msg).digest()) % N
    w = inv(s, N)
    u1 = h * w % N
    u2 = r * w % N
    pt = add(mul(u1, G), mul(u2, pub))
    if pt is None:
        return False
    return pt[0] % N == r


def sig_to_bytes(sig):
    """JWS 的 ES256 签名值：r 与 s 各 32 字节定长拼接，共 64 字节。"""
    r, s = sig
    return r.to_bytes(32, 'big') + s.to_bytes(32, 'big')
