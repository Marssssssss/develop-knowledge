"""
ECH / TLS 1.3 的 wire codec —— 基础类型、Reader 与 ClientHello（RFC 8446 §4.1.2）

ClientHello（**不含** 4 字节握手头）必须逐字节忠实：ECH 的 AAD 就是这个结构的序列化
结果，只做个「像 TLS」的近似会让 ClientHelloOuterAAD 对不上，Open 必然失败。

所有长度前缀都做边界检查：这一层是整个 ECH 里唯一对外部输入（DNS 记录、对端
ClientHello）做解析的地方，越界读就是可得的内存安全问题。
"""
from __future__ import annotations

ECH_VERSION = 0xFE0D
ECH_EXT_TYPE = 0xFE0D               # encrypted_client_hello
ECH_OUTER_EXTENSIONS = 0xFD00       # ech_outer_extensions

KEM_ID_X25519 = 0x0020
KDF_ID_HKDF_SHA256 = 0x0001
AEAD_ID_CHACHA20POLY1305 = 0x0003

EXT_SERVER_NAME = 0x0000
EXT_SUPPORTED_VERSIONS = 0x002B
EXT_KEY_SHARE = 0x0033


class EchError(Exception):
    """ECH 层的解析 / 校验错误（对应 TLS 的 illegal_parameter 警报）"""


def u8(v: int) -> bytes:
    return v.to_bytes(1, "big")


def u16(v: int) -> bytes:
    return v.to_bytes(2, "big")


def vec8(data: bytes) -> bytes:
    return u8(len(data)) + data


def vec16(data: bytes) -> bytes:
    return u16(len(data)) + data


class Reader:
    """带边界检查的顺序读取器。"""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise EchError(f"长度越界：想读 {n} 字节，只剩 {len(self.data) - self.pos} 字节")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def opaque8(self) -> bytes:
        return self.take(self.u8())

    def opaque16(self) -> bytes:
        return self.take(self.u16())

    def rest(self) -> bytes:
        return self.take(len(self.data) - self.pos)

    def expect_end(self) -> None:
        if self.pos != len(self.data):
            raise EchError(f"尾部还有 {len(self.data) - self.pos} 字节未消费")


# ------------------------------------------------------------ ClientHello（RFC 8446 §4.1.2）

class ClientHello:
    """不含握手头的 ClientHello。extensions 是 [(type, data), ...] 有序列表。"""

    def __init__(self, random: bytes, session_id: bytes = b"",
                 cipher_suites=(0x1301, 0x1302, 0x1303), compression_methods: bytes = b"\x00",
                 extensions=()) -> None:
        if len(random) != 32:
            raise EchError("random 必须是 32 字节")
        self.legacy_version = 0x0303
        self.random = random
        self.session_id = session_id
        self.cipher_suites = list(cipher_suites)
        self.compression_methods = compression_methods
        self.extensions = list(extensions)

    def copy(self, **kw) -> "ClientHello":
        new = ClientHello(self.random, self.session_id, self.cipher_suites,
                          self.compression_methods, list(self.extensions))
        for k, v in kw.items():
            setattr(new, k, v)
        return new

    def encode(self) -> bytes:
        suites = b"".join(u16(c) for c in self.cipher_suites)
        exts = b"".join(u16(t) + vec16(d) for t, d in self.extensions)
        return (u16(self.legacy_version) + self.random + vec8(self.session_id)
                + vec16(suites) + vec8(self.compression_methods) + vec16(exts))

    @classmethod
    def decode(cls, data: bytes) -> "ClientHello":
        r = Reader(data)
        ch = cls._decode_body(r)
        r.expect_end()
        return ch

    @classmethod
    def _decode_body(cls, r: Reader) -> "ClientHello":
        """顺序解析一个 ClientHello，返回后 r.pos 停在它结束的位置。

        EncodedClientHelloInner 是「ClientHello | 全零填充」，服务端必须知道
        ClientHello 本身在哪里结束（且不能用试错法截断 —— 那是 O(n^2) 的 DoS 入口）。
        """
        if r.u16() != 0x0303:
            raise EchError("legacy_version 必须是 0x0303")
        random = r.take(32)
        session_id = r.opaque8()
        if len(session_id) > 32:
            raise EchError("legacy_session_id 超长")
        suite_blob = r.opaque16()
        if len(suite_blob) == 0 or len(suite_blob) % 2:
            raise EchError("cipher_suites 长度必须是正偶数")
        suites = [int.from_bytes(suite_blob[i:i + 2], "big")
                  for i in range(0, len(suite_blob), 2)]
        comp = r.opaque8()
        if not comp:
            raise EchError("legacy_compression_methods 不能为空")
        ext_blob = r.opaque16()
        er = Reader(ext_blob)
        extensions, seen = [], set()
        while er.pos < len(ext_blob):
            etype = er.u16()
            edata = er.opaque16()
            if etype in seen:
                raise EchError(f"扩展 0x{etype:04x} 重复出现")
            seen.add(etype)
            extensions.append((etype, edata))
        return cls(random, session_id, suites, comp, extensions)

    # ---- 扩展操作（顺序敏感：压缩 / 解压都依赖位置）

    def ext(self, etype: int):
        for t, d in self.extensions:
            if t == etype:
                return d
        return None

    def ext_index(self, etype: int) -> int:
        for i, (t, _) in enumerate(self.extensions):
            if t == etype:
                return i
        return -1

    def set_ext(self, etype: int, data: bytes) -> None:
        i = self.ext_index(etype)
        if i < 0:
            self.extensions.append((etype, data))
        else:
            self.extensions[i] = (etype, data)


