"""
TLS-ECH 自检的共用夹具与固定服务器密钥

服务器密钥对直接取自 **RFC 9180 附录 A.2** 的 ikmR/skRm/pkRm —— 这样 ECH 的端到端
用例与 HPKE 官方向量共用同一把密钥，任何一层错位都能立刻暴露。
"""

from __future__ import annotations

from ech import build_client_hello_outer
from ech_config import (AEAD_ID_CHACHA20POLY1305, ECH_EXT_TYPE, ECH_VERSION, EXT_KEY_SHARE,
                        EXT_SERVER_NAME, EXT_SUPPORTED_VERSIONS, KDF_ID_HKDF_SHA256,
                        KEM_ID_X25519, ClientHello, EchConfig, HpkeKeyConfig,
                        ech_inner_extension, ech_outer_extension,
                        parse_ech_outer_extension, vec16)
import hpke

H = bytes.fromhex

# RFC 9180 A.2 的密钥材料：E 是发送方临时密钥，R 是服务器长期密钥
IKM_E = H("909a9b35d3dc4713a5e72a4da274b55d3d3821a37e5d099e74a647db583a904b")
SK_E = H("f4ec9b33b792c372c1d2c2063507b684ef925b8c75a42dbcbf57d63ccd381600")
PK_E = H("1afa08d3dec047a643885163f1180476fa7ddb54c6a8029ea33f95796bf2ac4a")
IKM_R = H("1ac01f181fdf9f352797655161c58b75c656a6cc2716dcb66372da835542e1df")
SK_R = H("8057991eef8f1f1af18f4a9491d16a1ce333f695d4db8e38da75975c4478e0fb")
PK_R = H("4310ee97d88cc1f088a5576c77ab0cf5c3ac797f3d95139c6c84b5429c59662a")

CONFIG_ID = 0x7F
MAX_NAME = 64
PUBLIC_NAME = b"example.com"     # ClientHelloOuter 里露出来的名字（客户端面向的服务器）
SNI = b"secret.example.org"      # ClientHelloInner 里真正要隐藏的名字
SUITE = (KDF_ID_HKDF_SHA256, AEAD_ID_CHACHA20POLY1305)

SNI_EXT = vec16(b"\x00" + vec16(SNI))
PUBLIC_SNI_EXT = vec16(b"\x00" + vec16(PUBLIC_NAME))
KEY_SHARE_EXT = b"\x00\x1d" + b"\xAB" * 32


def make_config(config_id: int = CONFIG_ID, max_name: int = MAX_NAME,
                public_name: bytes = PUBLIC_NAME, version: int = ECH_VERSION) -> EchConfig:
    return EchConfig(HpkeKeyConfig(config_id, KEM_ID_X25519, PK_R, [SUITE]),
                     max_name, public_name, [], version)


def make_inner(random: bytes, session_id: bytes) -> ClientHello:
    """ClientHelloInner：真实的 SNI + inner 变体的 ECH 扩展。"""
    return ClientHello(random, session_id, (0x1301, 0x1302, 0x1303), b"\x00", [
        (EXT_SERVER_NAME, SNI_EXT),
        (EXT_SUPPORTED_VERSIONS, b"\x02\x03\x04"),
        (EXT_KEY_SHARE, KEY_SHARE_EXT),
        (ECH_EXT_TYPE, ech_inner_extension()),
    ])


def make_outer_template(random: bytes, session_id: bytes) -> ClientHello:
    """ClientHelloOuter 的模板：SNI 换成 public_name，且**没有** ECH 扩展。

    ECH 扩展由 client_encrypt 最后追加（RFC 9849 §6.1 要求它在所有其它扩展之后）。
    """
    return ClientHello(random, session_id, (0x1301, 0x1302, 0x1303), b"\x00", [
        (EXT_SERVER_NAME, PUBLIC_SNI_EXT),
        (EXT_SUPPORTED_VERSIONS, b"\x02\x03\x04"),
        (EXT_KEY_SHARE, KEY_SHARE_EXT),
    ])


def rebuild_ech_ext(outer: ClientHello, payload: bytes) -> bytes:
    """保持 (cipher_suite, config_id, enc) 不变，只换 payload。"""
    suite, cid, enc, _ = parse_ech_outer_extension(outer.ext(ECH_EXT_TYPE))
    return ech_outer_extension(suite, cid, enc, payload)


def encrypt_raw(outer_tpl: ClientHello, cfg, plaintext: bytes) -> ClientHello:
    """按 RFC 9849 §6.1.1 的流程封装一段自定义明文（用来构造恶意 EncodedClientHelloInner）。"""
    enc, ctx = hpke.setup_base_s(cfg.key_config.public_key, b"")
    zero = build_client_hello_outer(outer_tpl, cfg, SUITE, enc,
                                    b"\x00" * hpke.sealed_size(len(plaintext)))
    sealed = ctx.seal(zero.encode(), plaintext)
    return build_client_hello_outer(outer_tpl, cfg, SUITE, enc, sealed)
