"""RFC 6962 §3 的数据结构：LogID / SCT / STH 的 TLS 编码（§1.2 声明按 RFC 5246 §4 的约定）。

几个容易搞错的定长字段：
- `LogID` = `opaque key_id[32]`，是**日志公钥 DER（SubjectPublicKeyInfo）的 SHA-256**，
  不是日志的域名哈希、也不是证书哈希。
- `timestamp` 是 NTP 时间，**单位是毫秒**（uint64）。
- 变长字段按长度上界选前缀字节数：`ASN.1Cert<1..2^24-1>` 用 **3 字节**长度，
  `CtExtensions<0..2^16-1>` 用 **2 字节**长度。
- `digitally-signed struct` 前面是 2 字节的 `hash_alg || sig_alg`，后面是
  **2 字节长度 + 签名**。
"""

import hashlib
import struct

VERSION_V1 = 0
SIG_TYPE_CERTIFICATE_TIMESTAMP = 0
SIG_TYPE_TREE_HASH = 1
ENTRY_TYPE_X509 = 0
ENTRY_TYPE_PRECERT = 1

HASH_ALG_SHA256 = 4        # RFC 5246 §7.4.1.4.1 的 HashAlgorithm
SIG_ALG_ECDSA = 3
SIG_ALG_RSA = 1


def log_id(der_spki):
    """key_id = SHA-256(日志公钥的 DER 编码，SubjectPublicKeyInfo 形式)。"""
    return hashlib.sha256(der_spki).digest()


def u8(v):
    return struct.pack("!B", v)


def u16(v):
    return struct.pack("!H", v)


def u64(v):
    return struct.pack("!Q", v)


def vector24(data):
    """`opaque <1..2^24-1>`：3 字节长度。"""
    if not (1 <= len(data) <= (1 << 24) - 1):
        raise ValueError("ASN.1Cert 长度必须在 1..2^24-1")
    n = len(data)
    return bytes([n >> 16 & 0xFF, n >> 8 & 0xFF, n & 0xFF]) + data


def vector16(data):
    """`opaque <0..2^16-1>`：2 字节长度。"""
    if len(data) > (1 << 16) - 1:
        raise ValueError("extensions 过长")
    return u16(len(data)) + data


def signed_entry(entry_type, cert_der=None, issuer_key_hash=None, tbs=None):
    """LogEntry 里被签名的那一段：x509_entry 直接放证书，precert_entry 放密钥哈希 + TBSCertificate。"""
    if entry_type == ENTRY_TYPE_X509:
        return u16(ENTRY_TYPE_X509) + vector24(cert_der)
    if entry_type == ENTRY_TYPE_PRECERT:
        if len(issuer_key_hash) != 32:
            raise ValueError("issuer_key_hash 必须是 32 字节")
        return u16(ENTRY_TYPE_PRECERT) + issuer_key_hash + vector24(tbs)
    raise ValueError("unknown entry_type")


def sct_signing_input(timestamp, entry_type, entry, extensions=b""):
    """SCT 的 digitally-signed 输入（§3.2）：

        version ‖ signature_type ‖ timestamp ‖ entry_type ‖ signed_entry ‖ extensions
    """
    return (u8(VERSION_V1) + u8(SIG_TYPE_CERTIFICATE_TIMESTAMP) + u64(timestamp)
            + entry + vector16(extensions))


def build_sct(logid, timestamp, entry, signature, extensions=b"",
              hash_alg=HASH_ALG_SHA256, sig_alg=SIG_ALG_ECDSA):
    """SignedCertificateTimestamp 的完整编码。"""
    if len(logid) != 32:
        raise ValueError("LogID 必须是 32 字节")
    return (u8(VERSION_V1) + logid + u64(timestamp) + vector16(extensions)
            + u8(hash_alg) + u8(sig_alg) + u16(len(signature)) + signature)


def parse_sct(raw):
    """按字节解析 SCT，返回各字段（含剩余字节数校验）。"""
    if len(raw) < 1 + 32 + 8 + 2:
        raise ValueError("SCT 太短")
    off = 0
    version = raw[off]
    off += 1
    logid = raw[off:off + 32]
    off += 32
    timestamp = struct.unpack("!Q", raw[off:off + 8])[0]
    off += 8
    ext_len = struct.unpack("!H", raw[off:off + 2])[0]
    off += 2
    ext = raw[off:off + ext_len]
    off += ext_len
    hash_alg = raw[off]
    off += 1
    sig_alg = raw[off]
    off += 1
    sig_len = struct.unpack("!H", raw[off:off + 2])[0]
    off += 2
    sig = raw[off:off + sig_len]
    off += sig_len
    if off != len(raw):
        raise ValueError("SCT 尾部还有 %d 字节未解释" % (len(raw) - off))
    return dict(version=version, log_id=logid, timestamp=timestamp,
                extensions=ext, hash_alg=hash_alg, sig_alg=sig_alg, signature=sig)


def sth_signing_input(timestamp, tree_size, root_hash):
    """SignedTreeHead 的 digitally-signed 输入（§3.5）：

        version ‖ signature_type=tree_hash ‖ timestamp ‖ tree_size ‖ sha256_root_hash
    """
    if len(root_hash) != 32:
        raise ValueError("root_hash 必须是 32 字节")
    return (u8(VERSION_V1) + u8(SIG_TYPE_TREE_HASH) + u64(timestamp)
            + u64(tree_size) + root_hash)


def parse_sth_signature_input(raw):
    """反解 STH 签名输入，用于自检里的往返校验。"""
    if len(raw) != 1 + 1 + 8 + 8 + 32:
        raise ValueError("STH 签名输入应当正好 50 字节")
    return dict(version=raw[0], sig_type=raw[1],
                timestamp=struct.unpack("!Q", raw[2:10])[0],
                tree_size=struct.unpack("!Q", raw[10:18])[0],
                root_hash=raw[18:50])
