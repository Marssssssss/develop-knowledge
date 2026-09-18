"""
ECHConfig / ECHConfigList / ECHClientHello 的编解码（RFC 9849 §4-§5）

ECHConfig 从 DNS 的 HTTPS 记录里来，是**不可信输入**；这里把每条长度前缀都做了检查，
并把规范里「必须忽略/必须 abort」的几种情况实现成显式异常：
  * 不认识的 version  -> 靠 length 字段跳过（§4）
  * 重复的扩展         -> 拒绝整条 ECHConfig（§4.2）
  * 高位为 1 的强制扩展 -> 拒绝整条 ECHConfig（§4.2）
"""

from __future__ import annotations

# 从 ech_wire 转出（本仓库内部模块，调用方可以只 import ech_config）
from ech_wire import (AEAD_ID_CHACHA20POLY1305, ECH_EXT_TYPE, ECH_OUTER_EXTENSIONS,  # noqa: F401
                      ECH_VERSION, EXT_KEY_SHARE, EXT_SERVER_NAME,               # noqa: F401
                      EXT_SUPPORTED_VERSIONS, KDF_ID_HKDF_SHA256, KEM_ID_X25519,  # noqa: F401
                      ClientHello, EchError, Reader, u8, u16, vec8, vec16)        # noqa: F401

# ------------------------------------------------------------ ECHConfig（RFC 9849 §4）

class HpkeKeyConfig:
    def __init__(self, config_id: int, kem_id: int, public_key: bytes,
                 cipher_suites) -> None:
        self.config_id = config_id
        self.kem_id = kem_id
        self.public_key = public_key
        self.cipher_suites = list(cipher_suites)      # [(kdf_id, aead_id), ...]

    def encode(self) -> bytes:
        suites = b"".join(u16(k) + u16(a) for k, a in self.cipher_suites)
        return u8(self.config_id) + u16(self.kem_id) + vec16(self.public_key) + vec16(suites)

    @classmethod
    def decode(cls, r: Reader) -> "HpkeKeyConfig":
        config_id = r.u8()
        kem_id = r.u16()
        public_key = r.opaque16()
        blob = r.opaque16()
        if len(blob) < 4 or len(blob) % 4:
            raise EchError("cipher_suites 长度必须是 4 的倍数且至少 4 字节")
        suites = [(int.from_bytes(blob[i:i + 2], "big"),
                   int.from_bytes(blob[i + 2:i + 4], "big"))
                  for i in range(0, len(blob), 4)]
        return cls(config_id, kem_id, public_key, suites)


class EchConfig:
    def __init__(self, key_config: HpkeKeyConfig, maximum_name_length: int,
                 public_name: bytes, extensions=(), version: int = ECH_VERSION) -> None:
        self.version = version
        self.key_config = key_config
        self.maximum_name_length = maximum_name_length
        self.public_name = public_name
        self.extensions = list(extensions)             # [(type, data), ...]

    def contents(self) -> bytes:
        exts = b"".join(u16(t) + vec16(d) for t, d in self.extensions)
        return (self.key_config.encode() + u8(self.maximum_name_length)
                + vec8(self.public_name) + vec16(exts))

    def encode(self) -> bytes:
        return u16(self.version) + vec16(self.contents())

    @classmethod
    def decode(cls, data: bytes) -> "EchConfig":
        r = Reader(data)
        cfg = cls._decode_from(r)
        r.expect_end()
        return cfg

    @classmethod
    def _decode_from(cls, r: Reader) -> "EchConfig":
        """从「version 字段」处开始读一条 ECHConfig（ECHConfigList 里连续排列）。"""
        version = r.u16()
        if version != ECH_VERSION:
            raise EchError(f"不支持的 ECH 版本 0x{version:04x}")
        body = Reader(r.opaque16())
        key_config = HpkeKeyConfig.decode(body)
        maximum_name_length = body.u8()
        public_name = body.opaque8()
        if not public_name:
            raise EchError("public_name 不能为空")
        ext_blob = body.opaque16()
        body.expect_end()
        er = Reader(ext_blob)
        extensions = []
        seen = set()
        while er.pos < len(ext_blob):
            etype = er.u16()
            edata = er.opaque16()
            if etype in seen:
                raise EchError("ECHConfig 扩展重复（RFC 9849 §4.2 禁止）")
            if etype & 0x8000:
                raise EchError(f"不认识的强制扩展 0x{etype:04x}，必须忽略整条 ECHConfig")
            seen.add(etype)
            extensions.append((etype, edata))
        return cls(key_config, maximum_name_length, public_name, extensions, version)


def encode_config_list(configs) -> bytes:
    return vec16(b"".join(c.encode() for c in configs))


def decode_config_list(data: bytes) -> list:
    """RFC 9849 §4：不认识的 version **靠 length 字段跳过**，而不是让整条列表失败。

    这正是 ECHConfig 里那个看似冗余的 length 字段存在的唯一理由 —— 客户端升级期
    必须能忽略未来的新版本，继续用列表里自己支持的那些配置。
    """
    r = Reader(data)
    blob = r.opaque16()
    r.expect_end()
    ir = Reader(blob)
    out = []
    while ir.pos < len(blob):
        version = int.from_bytes(ir.data[ir.pos:ir.pos + 2], "big")
        if version != ECH_VERSION:
            ir.u16()
            ir.opaque16()                      # 跳过这条
            continue
        out.append(EchConfig._decode_from(ir))
    return out


# ------------------------------------------------------------ ECHClientHello（RFC 9849 §5）

def ech_inner_extension() -> bytes:
    """inner 变体：只有一个 type 字节。存在的意义只是「服务端不许回没提议过的扩展」。"""
    return b"\x01"


def ech_outer_extension(cipher_suite, config_id: int, enc: bytes, payload: bytes) -> bytes:
    kdf_id, aead_id = cipher_suite
    return (b"\x00" + u16(kdf_id) + u16(aead_id) + u8(config_id)
            + vec16(enc) + vec16(payload))


def parse_ech_outer_extension(data: bytes):
    """返回 (cipher_suite, config_id, enc, payload)。"""
    r = Reader(data)
    if r.u8() != 0x00:
        raise EchError("不是 outer 变体的 ECH 扩展")
    suite = (r.u16(), r.u16())
    config_id = r.u8()
    enc = r.opaque16()
    payload = r.opaque16()
    r.expect_end()
    return suite, config_id, enc, payload