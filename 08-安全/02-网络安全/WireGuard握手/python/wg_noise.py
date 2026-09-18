"""
WireGuard 握手与传输消息（Noise_IKpsk2）—— 线格式、状态机、防 DoS cookie、定时器

依据（两侧都实际读过）：
  - Noise Protocol Framework 规范：IK／IKpsk2 模式、MixHash／MixKey／MixKeyAndHash、
    Split()，以及「PSK 握手里每个 e token 在 MixHash 之后还要 MixKey(e.public_key)」
  - 内核实现 drivers/net/wireguard/{messages.h,noise.c,cookie.c}：协议常量、消息
    结构体、kdf 调用形态、cookie 的 MAC 算法与全部定时器取值

纯标准库：BLAKE2s 来自 hashlib（见 wg_kdf.py），X25519 与 ChaCha20-Poly1305 为
本目录 wg_crypto.py 的纯 Python 实现。消息布局与内核结构体逐字段对齐（自检核对字节数）。
"""

from __future__ import annotations

import struct

from wg_crypto import aead_decrypt, aead_encrypt, x25519, x25519_public
from wg_kdf import HASH_LEN, SYM_LEN, blake2s, kdf, tai64n_newer, tai64n_now

# ------------------------------------------------------------
# 1. 协议常量（内核 noise.c / messages.h 原值）
# ------------------------------------------------------------

HANDSHAKE_NAME = b"Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s"     # 37 字节，无结尾 NUL
IDENTIFIER_NAME = b"WireGuard v1 zx2c4 Jason@zx2c4.com"       # 34 字节
PUB_LEN, TAG_LEN, TS_LEN = 32, 16, 12

M_INITIATION, M_RESPONSE, M_COOKIE, M_DATA = 1, 2, 3, 4
COOKIE_LEN, COOKIE_NONCE_LEN = 16, 24
COOKIE_SECRET_MAX_AGE, COOKIE_SECRET_LATENCY = 2 * 60, 5
MAC1_LABEL, COOKIE_LABEL = b"mac1----", b"cookie--"
COOKIE_LEN = 16
PADDING_MULTIPLE = 16

REKEY_TIMEOUT, REKEY_AFTER_TIME, REJECT_AFTER_TIME = 5, 120, 180
KEEPALIVE_TIMEOUT, INITIATIONS_PER_SECOND = 10, 50
MAX_TIMER_HANDSHAKES = 90 // REKEY_TIMEOUT            # = 18
REKEY_AFTER_MESSAGES = 1 << 60
COUNTER_BITS_TOTAL, COUNTER_REDUNDANT_BITS = 8192, 64
COUNTER_WINDOW_SIZE = COUNTER_BITS_TOTAL - COUNTER_REDUNDANT_BITS     # = 8128

# 消息总长 = 内核结构体的 sizeof（header 4B / index 4B / 字段 / authtag 16B / macs 32B）
LEN_INITIATION = 4 + 4 + PUB_LEN + (PUB_LEN + TAG_LEN) + (TS_LEN + TAG_LEN) + 32
LEN_RESPONSE = 4 + 4 + 4 + PUB_LEN + TAG_LEN + 32
LEN_COOKIE = 4 + 4 + COOKIE_NONCE_LEN + (COOKIE_LEN + TAG_LEN)
LEN_DATA_HEADER = 4 + 4 + 8


def handshake_init() -> tuple[bytes, bytes]:
    """返回 (ck0, h0)。

    与纯 Noise 的差异就在这里：Noise 是 h = HASH(protocol_name)（不足则补零），
    WireGuard 是 ck0 = HASH(handshake_name)、h0 = HASH(ck0 ‖ identifier_name)。
    多出的标识串把「具体实现」也绑进握手哈希，避免同名构造串的不同实现互通。
    """
    ck0 = blake2s(HANDSHAKE_NAME)
    return ck0, blake2s(ck0 + IDENTIFIER_NAME)


# ------------------------------------------------------------
# 2. 握手状态（Noise SymmetricState 的 WireGuard 版）
# ------------------------------------------------------------

class Symmetric:
    """持有 ck / h / k 与非非计数，对应 Noise 的 HandshakeState + SymmetricState。

    每个混合操作都直接写在握手流程里（而不是藏在「advance pattern」里），因为
    WireGuard 把 IKpsk2 硬编码在代码中。
    """

    def __init__(self, peer_static_pub: bytes):
        self.ck, self.h = handshake_init()
        self.k: bytes | None = None
        self.peer_static = peer_static_pub
        self.mix_hash(peer_static_pub)          # IK 的 pre-message: <- s

    def mix_hash(self, data: bytes) -> None:
        self.h = blake2s(self.h + data)

    def mix_key(self, ikm: bytes) -> None:
        self.ck, self.k = kdf(self.ck, ikm, HASH_LEN, SYM_LEN)

    def mix_key_and_hash(self, ikm: bytes) -> None:
        self.ck, temp_h, self.k = kdf(self.ck, ikm, HASH_LEN, HASH_LEN, SYM_LEN)
        self.mix_hash(temp_h)

    def mix_ephemeral(self, pub: bytes) -> None:
        """PSK 握手特有：e token 先 MixHash，再用同一公钥做 MixKey（只更新 ck）。"""
        self.mix_hash(pub)
        self.ck = kdf(self.ck, pub, HASH_LEN)[0]

    def encrypt(self, plain: bytes) -> bytes:
        ct = plain if self.k is None else \
            aead_encrypt(self.k, b"\x00" * 12, self.h, plain)
        self.mix_hash(ct)
        return ct

    def decrypt(self, ct: bytes) -> bytes:
        plain = ct if self.k is None else \
            aead_decrypt(self.k, b"\x00" * 12, self.h, ct)
        self.mix_hash(ct)
        return plain

    def split(self) -> tuple[bytes, bytes]:
        """Split()：HKDF(ck, 空输入, 2) → (发起方向, 响应方向) 两个传输密钥。"""
        return kdf(self.ck, b"", SYM_LEN, SYM_LEN)


class Peer:
    """两端通用的对端记录（简化为演示所需字段）。"""

    def __init__(self, static_priv: bytes, static_pub: bytes,
                 remote_static: bytes, psk: bytes):
        self.static_priv, self.static_pub = static_priv, static_pub
        self.remote_static, self.psk = remote_static, psk
        self.precomputed_ss = x25519(static_priv, remote_static)
        self.latest_timestamp: bytes | None = None


# ------------------------------------------------------------
# 3. 握手消息
# ------------------------------------------------------------

def _header(msg_type: int) -> bytes:
    """type 是 __le32：低字节为类型，高 3 字节保留为零（内核用 LE 省掉显式置零）。"""
    return struct.pack("<I", msg_type)


def create_initiation(peer: Peer, eph_priv: bytes, sender_index: int,
                      timestamp: bytes | None = None) -> tuple[bytes, Symmetric, bytes]:
    """构造 MESSAGE_HANDSHAKE_INITIATION：e → es → s → ss → {t}。"""
    sy = Symmetric(peer.remote_static)
    eph_pub = x25519_public(eph_priv)
    sy.mix_ephemeral(eph_pub)                                   # e
    sy.mix_key(x25519(eph_priv, peer.remote_static))            # es
    enc_static = sy.encrypt(peer.static_pub)                    # s（加密后 48 字节）
    sy.mix_key(peer.precomputed_ss)                             # ss（可用长期缓存值）
    ts = timestamp if timestamp is not None else tai64n_now()
    enc_ts = sy.encrypt(ts)                                     # {t}
    body = (_header(M_INITIATION) + struct.pack("<I", sender_index) +
            eph_pub + enc_static + enc_ts)
    return body + macs_for(body, peer.remote_static), sy, eph_pub


def consume_initiation(peer: Peer, msg: bytes,
                       peer_lookup) -> tuple[Peer, Symmetric, int, bytes]:
    """响应方处理发起消息，返回 (匹配到的对端, 状态, 发起方序号, 加密的静态公钥)。

    顺序有意与 create_initiation 镜像：先用 MAC1 廉价筛掉非本机目标的包，
    再做 es 解密拿到发起方静态公钥，最后查表。未知对端在这一步被丢弃。
    """
    if len(msg) != LEN_INITIATION:
        raise ValueError(f"bad initiation length {len(msg)}")
    if not check_mac1(msg, peer.static_pub):
        raise ValueError("MAC1 mismatch: packet not addressed to this responder")
    eph_pub = msg[8:8 + PUB_LEN]
    sy = Symmetric(peer.static_pub)          # 响应方视角的 pre-message = 自方静态公钥
    sy.mix_ephemeral(eph_pub)                                # e
    sy.mix_key(x25519(peer.static_priv, eph_pub))            # es（响应方视角）
    off = 8 + PUB_LEN
    initiator_static = sy.decrypt(msg[off:off + PUB_LEN + TAG_LEN])
    off += PUB_LEN + TAG_LEN
    matched = peer_lookup(initiator_static)
    if matched is None:
        raise ValueError("unknown initiator static key")
    sy.mix_key(matched.precomputed_ss)                       # ss
    ts = sy.decrypt(msg[off:off + TS_LEN + TAG_LEN])
    if matched.latest_timestamp is not None and \
            not tai64n_newer(ts, matched.latest_timestamp):
        raise ValueError("handshake replay: TAI64N timestamp not newer")
    return matched, sy, struct.unpack("<I", msg[4:8])[0], initiator_static


def create_response(peer: Peer, initiator_eph: bytes, initiator_static: bytes,
                    sy: Symmetric, receiver_index: int, sender_index: int,
                    eph_priv: bytes) -> tuple[bytes, Symmetric]:
    """构造 MESSAGE_HANDSHAKE_RESPONSE：e → ee → se → psk → {}。

    两个容易搞混的「静态公钥」：se 用**发起方**的静态公钥（响应方是在解开
    encrypted_static 时才拿到的），而 MAC1 以**收件方**（即发起方）的静态公钥为密钥。
    """
    eph_pub = x25519_public(eph_priv)
    sy.mix_ephemeral(eph_pub)                                 # e
    sy.mix_key(x25519(eph_priv, initiator_eph))               # ee
    sy.mix_key(x25519(eph_priv, initiator_static))            # se
    sy.mix_key_and_hash(peer.psk)                             # psk（IKpsk2 的关键一步）
    body = (_header(M_RESPONSE) + struct.pack("<I", sender_index) +
            struct.pack("<I", receiver_index) + eph_pub + sy.encrypt(b""))
    return body + macs_for(body, initiator_static), sy


def consume_response(peer: Peer, sy: Symmetric, eph_priv: bytes, msg: bytes
                     ) -> tuple[bytes, bytes]:
    """发起方处理响应消息，返回 (发起→响应, 响应→发起) 两个传输密钥。"""
    if len(msg) != LEN_RESPONSE:
        raise ValueError(f"bad response length {len(msg)}")
    if not check_mac1(msg, peer.static_pub):
        raise ValueError("MAC1 mismatch on response")
    re_pub = msg[12:12 + PUB_LEN]
    sy.mix_ephemeral(re_pub)                                # e
    sy.mix_key(x25519(eph_priv, re_pub))                    # ee
    sy.mix_key(x25519(peer.static_priv, re_pub))            # se（发起方视角）
    sy.mix_key_and_hash(peer.psk)
    sy.decrypt(msg[12 + PUB_LEN:12 + PUB_LEN + TAG_LEN])    # {} 空负载的认证
    return sy.split()


# ------------------------------------------------------------
# 4. 消息 MAC1 / MAC2（cookie 机制的上半部分，见 wg_cookie.py）
# ------------------------------------------------------------

def mac1_key(responder_static_pub: bytes) -> bytes:
    """BLAKE2s(\"mac1----\" ‖ 响应方静态公钥)，32 字节（内核 precompute_key）。"""
    return blake2s(MAC1_LABEL + responder_static_pub)


def compute_mac1(msg: bytes, responder_static_pub: bytes) -> bytes:
    """MAC1 = keyed-BLAKE2s(mac1_key, 消息中 MAC1 之前的全部字节, 16B)。"""
    return blake2s(msg[:len(msg) - 32], COOKIE_LEN, mac1_key(responder_static_pub))


def compute_mac2(msg: bytes, cookie: bytes) -> bytes:
    return blake2s(msg[:len(msg) - 16], COOKIE_LEN, cookie)


def macs_for(msg_without_macs: bytes, responder_static_pub: bytes,
             cookie: bytes | None = None) -> bytes:
    """把 MAC1｜MAC2 追加到消息尾部；无 cookie 时 MAC2 为全零（内核行为）。

    两个 MAC 的输入都是「本字段之前的全部字节」，所以这里先补零占位再截断 ——
    与内核把 MAC 字段留在结构体里、按 offsetof 计算长度的做法一致。
    """
    m1 = compute_mac1(msg_without_macs + b"\x00" * 32, responder_static_pub)
    m2 = compute_mac2(msg_without_macs + m1 + b"\x00" * 16, cookie) if cookie else \
        b"\x00" * COOKIE_LEN
    return m1 + m2


def check_mac1(msg: bytes, responder_static_pub: bytes) -> bool:
    return compute_mac1(msg, responder_static_pub) == msg[-32:-16]
