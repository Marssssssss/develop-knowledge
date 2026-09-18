"""
TLS Encrypted Client Hello 的客户端与服务端流程（RFC 9849 §5-§7）

客户端（§6.1）:
    EncodedClientHelloInner = (ClientHelloInner 清空 legacy_session_id) | zeros[padding]
    先按密文长度 L 填一段 L 个零的占位 payload 算出 ClientHelloOuterAAD，
    再 final_payload = HPKE.Seal(ClientHelloOuterAAD, EncodedClientHelloInner)，
    最后把占位换掉 —— 两段等长，所以不用重算任何长度前缀。

服务端（§7.1 / §7.2）:
    Open(ClientHelloOuterAAD, payload) → 取回 legacy_session_id（从 outer 抄回来）
    → 展开 ech_outer_extensions → 得到 ClientHelloInner
    再用 accept_confirmation 在 ServerHello.random 里告诉客户端「我解开了」。

两处「容易被忽略但会直接变成安全问题」的点：

* ClientHelloOuterAAD 里 payload 必须是**等长的零**。这样 AAD 才与最终发出的
  ClientHelloOuter 等长且不改动任何长度前缀 —— 攻击者替换 payload 会直接导致 Open 失败。
* ech_outer_extensions 的展开必须做顺序检查（§5.1 列了 4 条 MUST abort），
  否则攻击者可以用一个很小的 ClientHelloOuter 解压出巨大的 ClientHelloInner（放大攻击，
  §10.12.4）。本实现把 4 条逐条实现成显式的 raise。
"""

from __future__ import annotations

import hashlib
import hmac

import hpke
from ech_config import (ECH_EXT_TYPE, ECH_OUTER_EXTENSIONS, EXT_SERVER_NAME, ClientHello,
                        EchError, Reader, ech_outer_extension,
                        parse_ech_outer_extension, u16)


# ------------------------------------------------------------ TLS 1.3 HKDF-Expand-Label（RFC 8446 §7.1）

def hkdf_expand_label(secret: bytes, label: bytes, context: bytes, length: int) -> bytes:
    full = b"tls13 " + label
    hkdf_label = u16(length) + bytes([len(full)]) + full + bytes([len(context)]) + context
    return hpke.expand(secret, hkdf_label, length)


# ------------------------------------------------------------ 填充（§6.1.3）

def name_padding(inner: ClientHello, maximum_name_length: int) -> int:
    """server_name 的填充量：有 SNI 就补到 M 字节名字的长度，没有就补 M+9。

    注意 server_name 的嵌套：extension_data 是 ServerNameList<1..2^16-1>，
    里面每一项才是 (name_type u8, HostName<1..2^16-1>)。
    """
    sni = inner.ext(EXT_SERVER_NAME)
    if sni is None:
        return maximum_name_length + 9
    r = Reader(sni)
    sr = Reader(r.opaque16())
    r.expect_end()
    if sr.u8() != 0:                       # name_type 必须是 host_name(0)
        raise EchError("server_name 扩展类型不是 host_name")
    name = sr.opaque16()
    sr.expect_end()
    return max(0, maximum_name_length - len(name))


def round_to_32(length: int) -> int:
    """§6.1.3 第 2 步：N = 31 - ((L - 1) % 32)，把总长度凑成 32 的倍数。"""
    return 31 - ((length - 1) % 32)


def encoded_client_hello_inner(inner: ClientHello, padding: int = 0) -> bytes:
    """清空 legacy_session_id（§5.1）+ 追加全零填充。"""
    return inner.copy(session_id=b"").encode() + b"\x00" * padding


# ------------------------------------------------------------ 扩展压缩（§5.1）

def compress_inner(inner: ClientHello, outer: ClientHello, names) -> ClientHello:
    """把 outer 里必然重复的扩展从 inner 删掉，原位插入一条 ech_outer_extensions。

    注意 server_name **不要**放进 names：它的长度正是要隐藏的东西，
    删掉它等于放弃 name_padding 的作用（真实客户端也不会压它）。
    """
    names = [n for n in names if inner.ext_index(n) >= 0 and outer.ext_index(n) >= 0]
    if not names:
        return inner.copy()
    at = min(inner.ext_index(n) for n in names)
    out = []
    for i, (t, d) in enumerate(inner.extensions):
        if t in names:
            if i == at:                    # 在被删的第一个扩展的位置插入标记
                out.append((ECH_OUTER_EXTENSIONS, b"".join(u16(n) for n in names)))
            continue
        out.append((t, d))
    return inner.copy(extensions=out)


def decompress_inner(encoded: bytes, outer: ClientHello) -> ClientHello:
    """服务端展开 ech_outer_extensions，逐条执行 §5.1 的四条 MUST abort。"""
    inner = ClientHello.decode(encoded)
    names = inner.ext(ECH_OUTER_EXTENSIONS)
    if names is None:
        return inner
    if len(names) < 2 or len(names) % 2:
        raise EchError("OuterExtensions 长度非法")
    order = [int.from_bytes(names[i:i + 2], "big") for i in range(0, len(names), 2)]
    seen, restored, positions = set(), [], []
    for n in order:
        if n in seen:
            raise EchError("OuterExtensions 重复引用了同一个扩展")               # abort 2
        seen.add(n)
        if n == ECH_EXT_TYPE:
            raise EchError("OuterExtensions 不许引用 encrypted_client_hello")    # abort 3
        data = outer.ext(n)
        if data is None:
            raise EchError(f"ClientHelloOuter 里缺少被引用的扩展 0x{n:04x}")      # abort 1
        positions.append(outer.ext_index(n))
        restored.append((n, data))
    if positions != sorted(positions):
        raise EchError("被引用的扩展在 ClientHelloOuter 里的相对顺序不对")         # abort 4
    out = []
    for t, d in inner.extensions:
        if t == ECH_OUTER_EXTENSIONS:
            out.extend(restored)
        else:
            out.append((t, d))
    return inner.copy(extensions=out)


# ------------------------------------------------------------ 客户端（§6.1）

def build_client_hello_outer(outer_template: ClientHello, config, suite,
                             enc: bytes, payload: bytes) -> ClientHello:
    outer = outer_template.copy()
    outer.set_ext(ECH_EXT_TYPE,
                  ech_outer_extension(suite, config.key_config.config_id, enc, payload))
    return outer


def client_encrypt(inner: ClientHello, outer_template: ClientHello, config,
                   compress_names=()):
    """返回 (ClientHelloOuter, 客户端 Context, EncodedClientHelloInner)。"""
    suite = (hpke.KDF_ID_HKDF_SHA256, hpke.AEAD_ID_CHACHA20POLY1305)
    if suite not in config.key_config.cipher_suites:
        raise EchError("ECHConfig 不支持所选 KDF/AEAD 套件")

    inner_c = compress_inner(inner, outer_template, compress_names)
    encoded = encoded_client_hello_inner(
        inner_c, name_padding(inner, config.maximum_name_length))
    encoded += b"\x00" * round_to_32(len(encoded))

    enc, ctx = hpke.setup_base_s(config.key_config.public_key, b"")
    outer_zero = build_client_hello_outer(outer_template, config, suite, enc,
                                          b"\x00" * hpke.sealed_size(len(encoded)))
    final = ctx.seal(outer_zero.encode(), encoded)
    outer = build_client_hello_outer(outer_template, config, suite, enc, final)
    return outer, ctx, encoded


# ------------------------------------------------------------ 服务端（§7.1）

def server_decrypt(outer: ClientHello, sk_r: bytes, pk_r: bytes, config) -> ClientHello:
    """解封 ClientHelloOuter，返回 ClientHelloInner（含从 outer 抄回的 session_id）。"""
    ext = outer.ext(ECH_EXT_TYPE)
    if ext is None:
        raise EchError("ClientHelloOuter 缺少 encrypted_client_hello 扩展")
    suite, config_id, enc, payload = parse_ech_outer_extension(ext)
    if config_id != config.key_config.config_id:
        raise EchError(f"config_id 不匹配（试解密失败）：{config_id} != "
                       f"{config.key_config.config_id}")
    if suite != (hpke.KDF_ID_HKDF_SHA256, hpke.AEAD_ID_CHACHA20POLY1305):
        raise EchError("不支持的 HPKE 套件")

    outer_aad = outer.copy()
    outer_aad.set_ext(ECH_EXT_TYPE, ech_outer_extension(suite, config_id, enc,
                                                        b"\x00" * len(payload)))
    encoded = hpke.setup_base_r(enc, sk_r, pk_r, b"").open(outer_aad.encode(), payload)

    # §5.1：client_hello 之后必须全是零填充
    r = Reader(encoded)
    inner_c = ClientHello._decode_body(r)
    if any(b != 0 for b in encoded[r.pos:]):
        raise EchError("填充里出现非零字节（illegal_parameter）")
    inner = decompress_inner(encoded[:r.pos], outer)
    if inner.ext(ECH_EXT_TYPE) is None:
        raise EchError("ClientHelloInner 缺少 encrypted_client_hello 扩展")
    return inner.copy(session_id=outer.session_id)      # session_id 从 outer 抄回来


# ------------------------------------------------------------ 接受确认（§7.2 / §7.2.1）

def accept_confirmation(inner_random: bytes, transcript_ech_conf: bytes) -> bytes:
    """在 ServerHello.random 末 8 字节里回写，告诉客户端「ECH 被接受了」。"""
    prk = hpke.extract(b"", inner_random)
    return hkdf_expand_label(prk, b"ech accept confirmation", transcript_ech_conf, 8)


def hrr_accept_confirmation(inner_random: bytes, transcript_hrr_ech_conf: bytes) -> bytes:
    """HelloRetryRequest 版本：标签不同，值放扩展里而不是 random 里。"""
    prk = hpke.extract(b"", inner_random)
    return hkdf_expand_label(prk, b"hrr ech accept confirmation",
                             transcript_hrr_ech_conf, 8)


def transcript_hash(messages) -> bytes:
    h = hashlib.sha256()
    for m in messages:
        h.update(m)
    return h.digest()


def constant_time_eq(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)
