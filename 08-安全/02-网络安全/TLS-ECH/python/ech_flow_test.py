"""
TLS-ECH 自检（第二半）：端到端、篡改、ech_outer_extensions、接受确认

C. **协议必须成立的性质**：往返还原、篡改 AAD 必然失败、config_id 试解密失败、
   填充非零必须 abort、ech_outer_extensions 四条 MUST abort。
   这些全部写成「构造—破坏—必须失败」，而不是硬编码魔数 —— RFC 9849 没有官方向量。
D. **确认值**：确定性与区分度，并手工按 RFC 8446 §7.1 复算一遍 HkdfLabel 的编码。
"""

from __future__ import annotations

import hpke
from ech import (accept_confirmation, client_encrypt, compress_inner, constant_time_eq,
                 decompress_inner, encoded_client_hello_inner, hrr_accept_confirmation,
                 server_decrypt, transcript_hash)
from ech_config import (ECH_EXT_TYPE, ECH_OUTER_EXTENSIONS, EXT_KEY_SHARE, EXT_SERVER_NAME,
                        EXT_SUPPORTED_VERSIONS, ClientHello, EchError,
                        parse_ech_outer_extension, u16, vec16)
from ech_fixture import (CONFIG_ID, PK_R, PUBLIC_NAME, SK_R, SNI, encrypt_raw, make_config,
                         make_inner, make_outer_template, rebuild_ech_ext)


# ------------------------------------------------------------ C 类：端到端

def check_roundtrip() -> int:
    checks = 0
    cfg = make_config()
    rnd, sid = b"\x55" * 32, b"\x66" * 32
    inner = make_inner(rnd, sid)
    outer_tpl = make_outer_template(b"\x77" * 32, sid)

    outer, ctx_c, encoded = client_encrypt(inner, outer_tpl, cfg)
    clean = inner.copy(session_id=b"").encode()
    assert len(encoded) % 32 == 0                          # §6.1.3 第 2 步
    assert encoded[:len(clean)] == clean
    assert encoded[len(clean):] == b"\x00" * (len(encoded) - len(clean))
    checks += 3

    suite, cid, enc, payload = parse_ech_outer_extension(outer.ext(ECH_EXT_TYPE))
    assert cid == CONFIG_ID and suite == (0x0001, 0x0003)
    assert len(enc) == 32 and len(payload) == len(encoded) + 16
    assert ctx_c.seq == 1                                  # 客户端只 Seal 了一次
    checks += 3

    # AAD 与最终 ClientHelloOuter 等长（只差 payload 内容），所以不用重算长度前缀
    aad_like = outer.copy()
    aad_like.set_ext(ECH_EXT_TYPE, rebuild_ech_ext(outer, b"\x00" * len(payload)))
    assert len(aad_like.encode()) == len(outer.encode())
    checks += 1

    server_inner = server_decrypt(outer, SK_R, PK_R, cfg)
    assert server_inner.random == rnd
    assert server_inner.session_id == sid                  # 从 ClientHelloOuter 抄回来
    assert server_inner.cipher_suites == inner.cipher_suites
    assert server_inner.ext(EXT_SERVER_NAME) == inner.ext(EXT_SERVER_NAME)
    assert server_inner.ext(EXT_KEY_SHARE) == inner.ext(EXT_KEY_SHARE)
    assert server_inner.encode() == inner.encode()
    checks += 6

    # 真正要隐藏的 SNI 不出现在网络上；outer 里只有 public_name
    assert SNI not in outer.encode()
    assert PUBLIC_NAME in outer.encode()
    checks += 2

    # session_id 以 outer 为准（客户端在 ClientHelloOuter 里放的是随机值）
    outer_b = client_encrypt(inner, make_outer_template(b"\x77" * 32, b"\x99" * 32), cfg)[0]
    assert server_decrypt(outer_b, SK_R, PK_R, cfg).session_id == b"\x99" * 32
    checks += 1
    return checks


def check_tamper_and_wrong_key() -> int:
    checks = 0
    cfg = make_config()
    sid = b"\x66" * 32
    inner = make_inner(b"\x55" * 32, sid)
    outer_tpl = make_outer_template(b"\x77" * 32, sid)
    outer = client_encrypt(inner, outer_tpl, cfg)[0]

    # 篡改任何被 AAD 覆盖的字段 → Open 必须失败
    tampered = outer.copy()
    tampered.cipher_suites = [0x1301]
    try:
        server_decrypt(tampered, SK_R, PK_R, cfg)
        raise AssertionError("改过 cipher_suites 必须解封失败")
    except hpke.HpkeError:
        checks += 1

    tampered2 = outer.copy()
    tampered2.set_ext(EXT_SERVER_NAME, vec16(b"\x00" + vec16(b"attacker.example")))
    try:
        server_decrypt(tampered2, SK_R, PK_R, cfg)
        raise AssertionError("改过 SNI 必须解封失败")
    except hpke.HpkeError:
        checks += 1

    # 只改 payload 一个字节（payload 是 ECH 扩展的最后一个字段）
    raw = bytearray(outer.encode())
    payload_len = len(parse_ech_outer_extension(outer.ext(ECH_EXT_TYPE))[3])
    raw[len(raw) - payload_len] ^= 0x01
    try:
        server_decrypt(ClientHello.decode(bytes(raw)), SK_R, PK_R, cfg)
        raise AssertionError("改过 payload 必须解封失败")
    except hpke.HpkeError:
        checks += 1

    # config_id 不匹配 → 试解密失败（服务端会换下一个 ECHConfig 再试）
    try:
        server_decrypt(outer, SK_R, PK_R, make_config(config_id=CONFIG_ID ^ 0x01))
        raise AssertionError("config_id 不匹配必须失败")
    except EchError:
        checks += 1

    # 换一把私钥（config_id 相同）→ HPKE Open 失败
    other_sk, _ = hpke.derive_key_pair(b"\x00" * 32)
    try:
        server_decrypt(outer, other_sk, PK_R, cfg)
        raise AssertionError("私钥不对必须解封失败")
    except hpke.HpkeError:
        checks += 1

    # §5.1：填充里出现非零字节必须 abort
    dirty = bytearray(encoded_client_hello_inner(inner, 8))
    dirty[-1] = 0x01
    try:
        server_decrypt(encrypt_raw(outer_tpl, cfg, bytes(dirty)), SK_R, PK_R, cfg)
        raise AssertionError("非零填充必须 abort")
    except EchError:
        checks += 1
    return checks


def check_outer_extensions() -> int:
    checks = 0
    rnd, sid = b"\x88" * 32, b"\x99" * 32
    inner = make_inner(rnd, sid)
    outer = make_outer_template(b"\x77" * 32, sid)

    names = [EXT_SUPPORTED_VERSIONS, EXT_KEY_SHARE]
    squeezed = compress_inner(inner, outer, names)
    assert squeezed.ext(ECH_OUTER_EXTENSIONS) is not None
    assert squeezed.ext(EXT_SUPPORTED_VERSIONS) is None
    assert squeezed.ext(EXT_KEY_SHARE) is None
    assert squeezed.ext(EXT_SERVER_NAME) is not None      # SNI 不参与压缩
    assert squeezed.ext_index(ECH_OUTER_EXTENSIONS) == 1  # 插在被删的第一个扩展的位置
    checks += 5

    expanded = decompress_inner(squeezed.encode(), outer)
    assert expanded.extensions == inner.extensions        # 顺序完全还原
    checks += 1

    # abort 1：引用了 ClientHelloOuter 里没有的扩展
    bad = squeezed.copy()
    bad.set_ext(ECH_OUTER_EXTENSIONS, b"".join(u16(n) for n in names + [0x1234]))
    try:
        decompress_inner(bad.encode(), outer)
        raise AssertionError("引用缺失扩展必须 abort")
    except EchError:
        checks += 1
    # abort 2：重复引用
    bad2 = squeezed.copy()
    bad2.set_ext(ECH_OUTER_EXTENSIONS, u16(EXT_KEY_SHARE) * 2)
    try:
        decompress_inner(bad2.encode(), outer)
        raise AssertionError("重复引用必须 abort")
    except EchError:
        checks += 1
    # abort 3：引用 encrypted_client_hello
    bad3 = squeezed.copy()
    bad3.set_ext(ECH_OUTER_EXTENSIONS, u16(ECH_EXT_TYPE))
    try:
        decompress_inner(bad3.encode(), outer)
        raise AssertionError("引用 ECH 扩展必须 abort")
    except EchError:
        checks += 1
    # abort 4：相对顺序被换（outer 里 key_share 在 supported_versions 之后）
    bad4 = squeezed.copy()
    bad4.set_ext(ECH_OUTER_EXTENSIONS, u16(EXT_KEY_SHARE) + u16(EXT_SUPPORTED_VERSIONS))
    try:
        decompress_inner(bad4.encode(), outer)
        raise AssertionError("相对顺序不对必须 abort")
    except EchError:
        checks += 1

    # 压缩后 ClientHelloInner 变短（省掉重复扩展），端到端仍然逐字节还原
    cfg = make_config()
    _, _, plain_encoded = client_encrypt(inner, outer, cfg)
    enc_outer, _, encoded = client_encrypt(inner, outer, cfg, compress_names=names)
    assert len(encoded) + 32 <= len(plain_encoded)        # 净省下的字节数取整后仍是正的
    assert server_decrypt(enc_outer, SK_R, PK_R, cfg).encode() == inner.encode()
    checks += 2
    return checks


# ------------------------------------------------------------ D 类：确认值

def check_confirmation() -> int:
    checks = 0
    rnd = b"\xAA" * 32
    transcript = transcript_hash([b"ClientHelloInner", b"ServerHello"])

    ac = accept_confirmation(rnd, transcript)
    assert len(ac) == 8
    assert constant_time_eq(ac, accept_confirmation(rnd, transcript))
    assert not constant_time_eq(ac, accept_confirmation(b"\xAB" * 32, transcript))
    assert not constant_time_eq(ac, accept_confirmation(rnd, transcript + b"\x00"))
    checks += 4

    hrr = hrr_accept_confirmation(rnd, transcript)
    assert len(hrr) == 8 and not constant_time_eq(ac, hrr)   # 标签不同 → 值必然不同
    checks += 2

    # 手工按 RFC 8446 §7.1 复算一遍，确认 HkdfLabel 的编码正确
    full = b"tls13 ech accept confirmation"
    label = u16(8) + bytes([len(full)]) + full + bytes([len(transcript)]) + transcript
    assert constant_time_eq(ac, hpke.expand(hpke.extract(b"", rnd), label, 8))
    checks += 1
    return checks
