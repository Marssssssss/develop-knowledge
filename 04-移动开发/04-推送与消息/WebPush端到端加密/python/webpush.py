"""Web Push 端到端加密：RFC 8291（密钥管理）+ RFC 8188（aes128gcm 内容编码）。

整条链路（RFC 8291 §3.4 原文伪代码）：

    ecdh_secret = ECDH(as_private, ua_public)
    PRK_key     = HMAC-SHA-256(auth_secret, ecdh_secret)
    key_info    = "WebPush: info" || 0x00 || ua_public || as_public
    IKM         = HMAC-SHA-256(PRK_key, key_info || 0x01)
    PRK         = HMAC-SHA-256(salt, IKM)
    CEK         = HMAC-SHA-256(PRK, "Content-Encoding: aes128gcm" || 0x00 || 0x01)[:16]
    NONCE       = HMAC-SHA-256(PRK, "Content-Encoding: nonce"      || 0x00 || 0x01)[:12]

因为推送消息**只允许一条记录**（RFC 8291 §4），记录序号恒为 0，所以随机数是
NONCE 本身，不需要再与序号异或。
"""
import hashlib
import hmac
import os

import aesgcm
import p256

KEY_INFO_PREFIX = b'WebPush: info'
CEK_INFO = b'Content-Encoding: aes128gcm'
NONCE_INFO = b'Content-Encoding: nonce'
DEFAULT_RS = 4096
MAX_BODY = 4096
TAG_LEN = 16
HEADER_FIXED = 21          # salt(16) + rs(4) + idlen(1)


def hkdf_extract(salt, ikm):
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand_one(prk, info):
    """L 不超过 32 字节时的单轮 HKDF-Expand。"""
    return hmac.new(prk, info + b'\x01', hashlib.sha256).digest()


def ecdh(private_scalar, peer_public_bytes):
    """P-256 ECDH，返回共享secret的 X 坐标（32 字节）。"""
    peer = p256.bytes_to_point(peer_public_bytes)
    shared = p256.mul(private_scalar, peer)
    if shared is None:
        raise ValueError('ECDH produced the point at infinity')
    return shared[0].to_bytes(32, 'big')


def derive(ecdh_secret, auth_secret, ua_public, as_public, salt):
    """逐步复刻 RFC 8291 §3.4，返回全部中间值（便于与附录 A 对拍）。"""
    prk_key = hkdf_extract(auth_secret, ecdh_secret)
    key_info = KEY_INFO_PREFIX + b'\x00' + ua_public + as_public
    ikm = hkdf_expand_one(prk_key, key_info)
    prk = hkdf_extract(salt, ikm)
    cek = hkdf_expand_one(prk, CEK_INFO + b'\x00')[:16]
    nonce = hkdf_expand_one(prk, NONCE_INFO + b'\x00')[:12]
    return {
        'ecdh_secret': ecdh_secret,
        'prk_key': prk_key,
        'key_info': key_info,
        'ikm': ikm,
        'prk': prk,
        'cek': cek,
        'nonce': nonce,
    }


def encode_header(salt, rs, keyid):
    """RFC 8188 §2.1：salt(16) || rs(4, 大端) || idlen(1) || keyid。"""
    return salt + rs.to_bytes(4, 'big') + bytes([len(keyid)]) + keyid


def decode_header(raw):
    if len(raw) < HEADER_FIXED:
        raise ValueError('header shorter than 21 octets')
    salt = raw[0:16]
    rs = int.from_bytes(raw[16:20], 'big')
    idlen = raw[20]
    if len(raw) != HEADER_FIXED + idlen:
        raise ValueError('idlen disagrees with header length')
    return {'salt': salt, 'rs': rs, 'keyid': raw[21:21 + idlen]}


def padding_budget(rs, plaintext_len):
    """在 rs 之下还能塞多少填充字节（RFC 8291 §4 的不等式）。"""
    return rs - TAG_LEN - plaintext_len - 1


def max_plaintext(body_limit=MAX_BODY, rs=DEFAULT_RS, pad=0):
    """服务端保证支持 4096 字节 body 时，明文上限（§4 算的是 3993）。"""
    header = HEADER_FIXED + 65
    return body_limit - header - 1 - pad - TAG_LEN


def encrypt(ua_public, auth_secret, plaintext, rs=DEFAULT_RS, pad=0,
            as_private=None, salt=None):
    """应用服务器侧加密，返回完整的 body（header || 密文 || tag）。"""
    if as_private is None:
        as_private = 2 + int.from_bytes(os.urandom(32), 'big') % (p256.N - 3)
    if salt is None:
        salt = os.urandom(16)
    as_public = p256.point_to_bytes(p256.public_key(as_private))
    if isinstance(ua_public, tuple):
        ua_public = p256.point_to_bytes(ua_public)
    secret = ecdh(as_private, ua_public)
    d = derive(secret, auth_secret, ua_public, as_public, salt)
    if padding_budget(rs, len(plaintext)) < pad:
        raise ValueError('rs too small for plaintext + padding')
    record = plaintext + b'\x02' + b'\x00' * pad
    body = encode_header(salt, rs, as_public) + \
        aesgcm.gcm_encrypt(d['cek'], d['nonce'], record)
    return body, d


def parse_record(record):
    """按 RFC 8188 §2.2 拆开 `明文 || 0x02 || 填充`；分隔符不是 0x02 必须丢弃。"""
    idx = record.rfind(b'\x02')
    if idx < 0:
        raise ValueError('padding delimiter must be 0x02')
    return record[:idx], record[idx:]


def decrypt(ua_private, auth_secret, body):
    """用户代理侧解密。"""
    head = decode_header(body[:HEADER_FIXED + 65])
    as_public = head['keyid']
    ua_public = p256.point_to_bytes(p256.public_key(ua_private))
    secret = ecdh(ua_private, as_public)
    d = derive(secret, auth_secret, ua_public, as_public, head['salt'])
    record = aesgcm.gcm_decrypt(d['cek'], d['nonce'], body[HEADER_FIXED + 65:])
    plaintext, _tail = parse_record(record)
    return plaintext, d
