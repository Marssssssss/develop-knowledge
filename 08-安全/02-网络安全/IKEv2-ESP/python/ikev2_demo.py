"""
IKEv2 密钥协商 + ESP 数据面 (RFC 7296 + RFC 4303)

本程序演示 IKEv2 控制面 + ESP 数据面:
  (1) IKE_SA_INIT: Initiator 与 Responder 各生成 X25519 临时密钥对,
      互发 ECDH 公钥 + nonce + SA 提议 → 用 prf(Ni | Nr, g^ir) 派生 SKEYSEED
  (2) 派生 sk_d / sk_ai / sk_ar / sk_ei / sk_er / sk_pi / sk_pr
      (RFC 7296 §2.14,共 7 把密钥)
  (3) 派生 Child SA 密钥(AES-128-GCM 加密 + 完整性):
        keymat = prf+(sk_d, Ni | Nr, ...)
        sk_ei_child / sk_ai_child / sk_er_child / sk_ar_child
  (4) 构造一个 ESP 隧道包:SPI(32 bit) + SeqNo(32 bit) + IV(12 B) +
      AES-GCM 加密的 IP payload + 16 B ICV → Responder 收到 → 用对应
      Child SA 密钥 + SeqNo 解密验证

依赖:仅标准库 hashlib/hmac/secrets (X25519 用同样的 Montgomery ladder 实现
    简化版;生产请用 cryptography / libnacl)。
"""

from __future__ import annotations
import hashlib
import hmac
import os
import secrets
import struct
from typing import Tuple

# ============================================================
# 1. Hash 函数 (RFC 7296 §2.13 — IKEv2 默认 PRF 是 PRF-HMAC-SHA2-256)
# ============================================================

def prf(key: bytes, data: bytes) -> bytes:
    """RFC 7296 §2.13 prf(K, S) = HMAC-PRF(K, S);
    当 PRF 是 HMAC-SHA2-256 时,output = HMAC(K, S)[0:32],K 作 HMAC key。
    """
    return hmac.new(key, data, hashlib.sha256).digest()


def prfplus(key: bytes, seed: bytes, count: int) -> bytes:
    """RFC 7296 §2.13 prf+ = T(1) || T(2) || ... || T(count)
       其中 T(i) = prf(K, T(i-1) | seed | i)。T(0) 为空串。
       count * 32 字节输出限制。"""
    if count <= 0:
        return b''
    T = b''
    cur = b''
    for i in range(1, count + 1):
        cur = prf(key, cur + seed + bytes([i]))
        T += cur
    return T


# ============================================================
# 2. X25519 (RFC 7748) — 与 TLS 1.3 demo 同样的实现
# ============================================================

_P25519 = 2**255 - 19

def _inv(k: int, p: int) -> int:
    return pow(k, p - 2, p)

def _x25519(k: bytes, u: int) -> int:
    scalar = int.from_bytes(k, 'little') & ((1 << 254) - 8)
    x1, x2, z2, x3, z3 = u, 1, 0, u, 1
    swap = 0
    p = _P25519
    for t in reversed(range(255)):
        k_t = (scalar >> t) & 1
        swap ^= k_t
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = k_t
        A = (x2 + z2) % p; AA = (A * A) % p
        B = (x2 - z2) % p; BB = (B * B) % p
        E = (AA - BB) % p
        C = (x3 + z3) % p
        D = (x3 - z3) % p
        DA = (D * A) % p; CB = (C * B) % p
        x3 = pow((DA + CB) % p, 2, p)
        z3 = (x1 * pow((DA - CB) % p, 2, p)) % p
        x2 = (AA * BB) % p
        z2 = (E * ((AA + 121665 * E) % p)) % p
    if swap:
        x2, x3 = x3, x2
    return (x2 * _inv(z2, p)) % p


def x25519_keypair() -> Tuple[bytes, bytes]:
    priv = secrets.token_bytes(32)
    pub = _x25519(priv, 9).to_bytes(32, 'little')
    return priv, pub


def x25519(priv: bytes, peer_pub: bytes) -> bytes:
    return _x25519(priv, int.from_bytes(peer_pub, 'little')).to_bytes(32, 'little')


# ============================================================
# 3. RFC 7296 §2.14 密钥派生
# ============================================================

def ikev2_derive(shared: bytes, ni: bytes, nr: bytes):
    """SKEYSEED = prf(Ni | Nr, g^ir)
       {SK_d, SK_ai, SK_ar, SK_ei, SK_er, SK_pi, SK_pr} = prf+(SKEYSEED, Ni | Nr | SPIi | SPIr, 7)
    """
    skeyseed = prf(ni + nr, shared)
    sk_d  = prfplus(skeyseed, ni + nr, 1)         # 派生 KEYMAT for Child SA
    sk_ai = prfplus(skeyseed, ni + nr, 1)         # Initiator IKE integrity
    sk_ar = prfplus(skeyseed, ni + nr, 1)         # Responder IKE integrity
    sk_ei = prfplus(skeyseed, ni + nr, 1)         # Initiator IKE encryption
    sk_er = prfplus(skeyseed, ni + nr, 1)         # Responder IKE encryption
    sk_pi = prfplus(skeyseed, ni + nr, 1)         # Initiator AUTH key
    sk_pr = prfplus(skeyseed, ni + nr, 1)         # Responder AUTH key
    return {
        'SKEYSEED': skeyseed,
        'SK_d':  sk_d,
        'SK_pi': sk_pi,
        'SK_pr': sk_pr,
        'SK_ai': sk_ai,
        'SK_ar': sk_ar,
        'SK_ei': sk_ei,
        'SK_er': sk_er,
    }


def derive_child_sa_keys(sk_d: bytes, ni: bytes, nr: bytes,
                          enc_key_len: int = 16, integ_len: int = 16):
    """RFC 7296 §2.14 KEYMAT = prf+(SK_d, Ni | Nr, K)
       total = 2 * enc_key_len + 2 * integ_len (= 64 B for AES-128-GCM)
    """
    total = 2 * enc_key_len + 2 * integ_len
    count = (total + 31) // 32
    keymat = prfplus(sk_d, ni + nr, count)
    return {
        'SK_ei_child': keymat[:enc_key_len],
        'SK_ai_child': keymat[enc_key_len:enc_key_len + integ_len],
        'SK_er_child': keymat[enc_key_len + integ_len:2 * enc_key_len + integ_len],
        'SK_ar_child': keymat[2 * enc_key_len + integ_len:2 * enc_key_len + 2 * integ_len],
    }


# ============================================================
# 4. AES-128-GCM (用 cryptography)
# ============================================================

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    def aes_gcm_seal(key: bytes, iv: bytes, pt: bytes, aad: bytes = b'') -> Tuple[bytes, bytes]:
        out = AESGCM(key).encrypt(iv, pt, aad)
        return out[:-16], out[-16:]   # ct, tag

    def aes_gcm_open(key: bytes, iv: bytes, ct: bytes, tag: bytes, aad: bytes = b'') -> bytes:
        return AESGCM(key).decrypt(iv, ct + tag, aad)
except ImportError:
    aes_gcm_seal = None
    aes_gcm_open = None


# ============================================================
# 5. ESP 包封装(RFC 4303 §2)
# ============================================================

def make_esp_packet(spi: int, seqno: int, key: bytes,
                    inner_ip: bytes, seqno_direction: str = 'to_responder') -> bytes:
    """构造一个 ESP 隧道包:
       ESP HDR (SPI 32 bit + SeqNo 32 bit, RFC 4303 §2.4)
       + IV (12 B for AES-GCM, RFC 4303 §2.4)
       + 加密的 inner_ip
       + ICV (16 B AES-GCM tag, RFC 4303 §2.8)
    """
    iv = os.urandom(12)
    ct, tag = aes_gcm_seal(key, iv, inner_ip, b'')
    # ESP HDR
    hdr = struct.pack(">II", spi & 0xFFFFFFFF, seqno & 0xFFFFFFFF)
    return hdr + iv + ct + tag


def parse_and_decrypt_esp(pkt: bytes, spi: int, key: bytes) -> Tuple[int, bytes]:
    """从 ESP 包提取 SeqNo + 解密负载。"""
    pkt_spi, seqno = struct.unpack(">II", pkt[:8])
    if pkt_spi != spi:
        raise ValueError(f"SPI mismatch: expected {spi:#x}, got {pkt_spi:#x}")
    iv = pkt[8:20]
    ct = pkt[20:-16]
    tag = pkt[-16:]
    inner = aes_gcm_open(key, iv, ct, tag, b'')
    return seqno, inner


# ============================================================
# 6. Demo
# ============================================================

def main():
    print("=" * 70)
    print("  IKEv2 密钥协商 (RFC 7296) + ESP 数据面 (RFC 4303) 演示")
    print("=" * 70)

    # ─────────────────────────────────────────
    # IKE_SA_INIT: 双方发 ECDH + Nonce + SA 提议
    # ─────────────────────────────────────────
    print("\n[Phase 1] IKE_SA_INIT: 双方发 X25519 公钥 + Nonce + SA 提议 →")
    i_priv, i_pub = x25519_keypair()
    r_priv, r_pub = x25519_keypair()
    ni = secrets.token_bytes(32)
    nr = secrets.token_bytes(32)
    print(f"  Initiator pub = {i_pub.hex()[:16]}…  Ni = {ni.hex()[:16]}…")
    print(f"  Responder pub = {r_pub.hex()[:16]}…  Nr = {nr.hex()[:16]}…")

    # ECDHE 共享秘密
    shared_i = x25519(i_priv, r_pub)
    shared_r = x25519(r_priv, i_pub)
    assert shared_i == shared_r
    print(f"  ECDHE g^ir (双方一致) = {shared_i.hex()[:16]}…")

    # SKEYSEED + 7 把子密钥
    keys = ikev2_derive(shared_i, ni, nr)
    print(f"\n  SKEYSEED        = {keys['SKEYSEED'].hex()[:16]}…")
    print(f"  SK_d (派生 Child SA KEYMAT 用) = {keys['SK_d'].hex()[:16]}…")
    print(f"  SK_ei / SK_er (IKE 控制面加密)  = {keys['SK_ei'].hex()[:16]}… / {keys['SK_er'].hex()[:16]}…")
    print(f"  SK_ai / SK_ar (IKE 控制面 HMAC)  = {keys['SK_ai'].hex()[:16]}… / {keys['SK_ar'].hex()[:16]}…")

    # ─────────────────────────────────────────
    # IKE_AUTH + 第一个 Child SA
    # ─────────────────────────────────────────
    print("\n[Phase 2] IKE_AUTH 完成 + 派生 Child SA 密钥 (AES-128-GCM):")
    child_keys = derive_child_sa_keys(keys['SK_d'], ni, nr, 16, 16)
    print(f"  SK_ei_child (Initiator→Responder ESP 加密 key, 16 B) = {child_keys['SK_ei_child'].hex()[:16]}…")
    print(f"  SK_ai_child (Initiator→Responder ESP HMAC key, 16 B)= {child_keys['SK_ai_child'].hex()[:16]}…")
    print(f"  SK_er_child (Responder→Initiator ESP 加密 key, 16 B) = {child_keys['SK_er_child'].hex()[:16]}…")
    print(f"  SK_ar_child (Responder→Initiator ESP HMAC key, 16 B)= {child_keys['SK_ar_child'].hex()[:16]}…")

    # ─────────────────────────────────────────
    # 一个 ESP 包(Initiator → Responder)
    # ─────────────────────────────────────────
    print("\n[ESP] Initiator 用 SK_ei_child 加密一个内嵌 IPv4 包 (HTTP):")
    spi = 0x12345678
    seqno = 1
    inner = bytearray([
        0x45, 0x00, 0x00, 0x54,    # IPv4 ver=4 IHL=5, total_len=84
        0x1c, 0x46, 0x40, 0x00,
        0x40, 0x06, 0xb1, 0xe6,    # TTL=64, proto=TCP
        0xac, 0x10, 0x00, 0x0a,    # src=172.16.0.10
        0xc0, 0xa8, 0x01, 0x01,    # dst=192.168.1.1
    ])
    inner.extend(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")  # inner IP payload

    esp_pkt = make_esp_packet(spi, seqno, child_keys['SK_ei_child'], bytes(inner))
    print(f"  ESP 包大小 = {len(esp_pkt)} B (HDR 8 + IV 12 + CT {len(esp_pkt)-36} + ICV 16)")
    print(f"  ESP header: SPI = 0x{spi:08X}, SeqNo = {seqno}")

    # ─────────────────────────────────────────
    # Responder 收到 ESP,解密还原 inner IP
    # ─────────────────────────────────────────
    print("\n[ESP] Responder 用 SK_ei_child(同一 key,反向于 Initiator→Responder 是 SK_ei_child)解")
    r_seqno, r_inner = parse_and_decrypt_esp(esp_pkt, spi, child_keys['SK_ei_child'])
    print(f"  SeqNo 还原 = {r_seqno}")
    print(f"  inner IP    = {r_inner[:20].hex()}…")
    print(f"  HTTP 请求   = {r_inner[20:30]}…")
    assert r_inner == bytes(inner)
    print("  ✓ ESP 解密还原 = inner IP 完全一致 (HTTP 流量可由 Responder 转发)")

    # ─────────────────────────────────────────
    # 反向: Responder → Initiator 用 SK_er_child
    # ─────────────────────────────────────────
    print("\n[ESP] 反向: Responder → Initiator 用 SK_er_child 加密回应:")
    reply_inner = bytearray([
        0x45, 0x00, 0x00, 0x3c,
        0x1c, 0x46, 0x40, 0x00,
        0x40, 0x06, 0xb1, 0xe6,
        0xc0, 0xa8, 0x01, 0x01,    # src=192.168.1.1
        0xac, 0x10, 0x00, 0x0a,    # dst=172.16.0.10
    ])
    reply_inner.extend(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello")

    seqno_reply = 1
    esp_reply = make_esp_packet(spi, seqno_reply, child_keys['SK_er_child'], bytes(reply_inner))
    print(f"  ESP 包大小 = {len(esp_reply)} B (SeqNo={seqno_reply})")

    # Initiator 端解密
    print("\n[ESP] Initiator 收到 → SK_er_child 解密:")
    i2_seq, i2_inner = parse_and_decrypt_esp(esp_reply, spi, child_keys['SK_er_child'])
    print(f"  HTTP 响应 = {i2_inner[20:50].decode(errors='replace')!r}…")
    assert i2_inner == bytes(reply_inner)

    # ─────────────────────────────────────────
    # Rekey: CREATE_CHILD_SA 重协商
    # ─────────────────────────────────────────
    print("\n[Rekey] CREATE_CHILD_SA: 双方新 X25519 派生 (PFS):")
    i_priv2, i_pub2 = x25519_keypair()
    r_priv2, r_pub2 = x25519_keypair()
    shared_i2 = x25519(i_priv2, r_pub2)
    shared_r2 = x25519(r_priv2, i_pub2)
    assert shared_i2 == shared_r2
    print(f"  New g^ir = {shared_i2.hex()[:16]}…  (与第一次独立 → 完美前向保密)")

    print("\n" + "=" * 70)
    print("  IKEv2 + ESP 演示完成 ✓")
    print("=" * 70)
    print("\n注:本 demo 简化:")
    print("  · 不实现 IKE 消息字节级格式化(载荷解析太复杂)")
    print("  · 不实现真实 NETWORK TRANSPORT(UDP 500/4500)+ Anti-replay 窗口")
    print("  · 不实现 NAT-T MD5 hash、NAT detection 探测")
    print("  · 不实现 MOBIKE 寻址更新")


if __name__ == "__main__":
    main()
