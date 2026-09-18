"""
TLS-ECH 自检（第一半）：RFC 9180 官方向量 + 编解码 + 填充公式

A. **RFC 9180 附录 A.2 官方向量** —— HPKE 全套（DeriveKeyPair / Encap / KeySchedule /
   6 组加密 / 3 组 Export）。这是本 demo 唯一有权威期望值的部分。
B. **可由规范直接推出的等式**：padding 公式、round_to_32 的 32 字节对齐、
   EncodedClientHelloInner 必须清空 legacy_session_id。

协议层（端到端、篡改、ech_outer_extensions、确认值）在 ech_flow_test.py。
"""

from __future__ import annotations

import hpke
from ech import encoded_client_hello_inner, name_padding, round_to_32
from ech_config import (ECH_VERSION, EXT_SERVER_NAME, EchConfig, EchError, HpkeKeyConfig,
                        ClientHello, decode_config_list, ech_inner_extension,
                        encode_config_list, u16, vec16)
from ech_fixture import (CONFIG_ID, H, IKM_E, IKM_R, MAX_NAME, PK_E, PK_R, PUBLIC_NAME,
                         SK_E, SK_R, SNI, SUITE, make_config, make_inner)


# ------------------------------------------------------------ A 类：RFC 9180 A.2

def check_hpke_vectors() -> int:
    checks = 0
    info = H("4f6465206f6e2061204772656369616e2055726e")   # "Ode on a Grecian Urn"

    sk, pk = hpke.derive_key_pair(IKM_R)
    assert sk == SK_R and pk == PK_R
    sk_e, pk_e = hpke.derive_key_pair(IKM_E)
    assert sk_e == SK_E and pk_e == PK_E
    checks += 4

    enc, ss = hpke.encap(PK_R, ikm=IKM_E)                # enc 是**发送方**的临时公钥
    assert enc == PK_E
    assert ss.hex() == "0bbe78490412b4bbea4812666f7916932b828bba79942424abb65244930d69a7"
    assert hpke.decap(enc, SK_R, PK_R) == ss
    checks += 3

    ks = hpke.KeySchedule(ss, info)
    assert ks.key_schedule_context.hex() == (
        "00431df6cd95e11ff49d7013563baf7f11588c75a6611ee2a4404a49306ae4cf"
        "c5b69c5718a60cc5876c358d3f7fc31ddb598503f67be58ea1e798c0bb19eb9796")
    assert ks.secret.hex() == "5b9cd775e64b437a2335cf499361b2e0d5e444d5cb41a8a53336d8fe402282c6"
    assert ks.key.hex() == "ad2744de8e17f4ebba575b3f5f5a8fa1f69c2a07f6e7500bc60ca6e3e3ec1c91"
    assert ks.base_nonce.hex() == "5c4d98150661b848853b547f"
    assert ks.exporter_secret.hex() == (
        "a3b010d4994890e2c6968a36f64470d3c824c8f5029942feb11e7a74b2921922")
    checks += 5

    pt = H("4265617574792069732074727574682c20747275746820626561757479")
    ctx = hpke.setup_base_r(enc, SK_R, PK_R, info)
    cases = [
        (0, "436f756e742d30", "1c5250d8034ec2b784ba2cfd69dbdb8af406cfe3ff938e131f0def8c8b60b4db21993c62ce81883d2dd1b51a28"),
        (1, "436f756e742d31", "6b53c051e4199c518de79594e1c4ab18b96f081549d45ce015be002090bb119e85285337cc95ba5f59992dc98c"),
        (2, "436f756e742d32", "71146bd6795ccc9c49ce25dda112a48f202ad220559502cef1f34271e0cb4b02b4f10ecac6f48c32f878fae86b"),
        (4, "436f756e742d34", "63357a2aa291f5a4e5f27db6baa2af8cf77427c7c1a909e0b37214dd47db122bb153495ff0b02e9e54a50dbe16"),
        (255, "436f756e742d323535", "18ab939d63ddec9f6ac2b60d61d36a7375d2070c9b683861110757062c52b8880a5f6b3936da9cd6c23ef2a95c"),
        (256, "436f756e742d323536", "7a4a13e9ef23978e2c520fd4d2e757514ae160cd0cd05e556ef692370ca53076214c0c40d4c728d6ed9e727a5b"),
    ]
    for seq, aad, ct in cases:
        ctx.seq = seq                                 # 向量跨了 0/1/2/4/255/256，序号直接指定
        got = ctx.seal(H(aad), pt)
        assert got.hex() == ct, f"seq {seq}"
        ctx.seq = seq
        assert ctx.open(H(aad), got) == pt
    checks += len(cases)

    ctx2 = hpke.setup_base_s(PK_R, info, ikm=IKM_E)[1]
    assert ctx2.export(b"", 32).hex() == \
        "4bbd6243b8bb54cec311fac9df81841b6fd61f56538a775e7c80a9f40160606e"
    assert ctx2.export(H("00"), 32).hex() == \
        "8c1df14732580e5501b00f82b10a1647b40713191b7c1240ac80e2b68808ba69"
    assert ctx2.export(H("54657374436f6e74657874"), 32).hex() == \
        "5acb09211139c43b3090489a9da433e8a30ee7188ba8b0a9a1ccf0c229283e53"
    checks += 3
    return checks


# ------------------------------------------------------------ 编解码

def check_codec() -> int:
    checks = 0

    cfg = make_config()
    wire = cfg.encode()
    assert wire[:2] == u16(ECH_VERSION)
    assert int.from_bytes(wire[2:4], "big") == len(wire) - 4      # length 覆盖 contents
    back = EchConfig.decode(wire)
    assert back.encode() == wire
    assert back.public_name == PUBLIC_NAME and back.maximum_name_length == MAX_NAME
    assert back.key_config.cipher_suites == [SUITE]
    checks += 4

    other = make_config(config_id=0x01)
    lst = encode_config_list([cfg, other])
    assert [c.key_config.config_id for c in decode_config_list(lst)] == [CONFIG_ID, 0x01]
    # 未知版本靠 length 跳过（RFC 9849 §4），不能让整条列表失败
    unknown = u16(0xFE0E) + vec16(b"\xAA" * 5)
    got = decode_config_list(vec16(unknown + cfg.encode()))
    assert len(got) == 1 and got[0].encode() == wire
    checks += 2

    ch = make_inner(b"\x11" * 32, b"\x22" * 32)
    assert ClientHello.decode(ch.encode()).extensions == ch.extensions
    assert ClientHello.decode(ch.encode()).session_id == b"\x22" * 32
    checks += 2

    # 尾部多一字节必须报错（否则「解析成功」的含义就不成立）
    try:
        ClientHello.decode(ch.encode() + b"\x00")
        raise AssertionError("尾部多余字节必须报错")
    except EchError:
        checks += 1
    # 重复扩展必须报错
    dup = ClientHello(b"\x11" * 32, b"", (0x1301,), b"\x00",
                      [(0x002B, b"\x02\x03\x04"), (0x002B, b"\x02\x03\x04")])
    try:
        ClientHello.decode(dup.encode())
        raise AssertionError("重复扩展必须报错")
    except EchError:
        checks += 1
    # 未知强制扩展（高位为 1）必须让整条 ECHConfig 被忽略
    mandatory = EchConfig(HpkeKeyConfig(CONFIG_ID, 0x0020, PK_R, [SUITE]),
                          MAX_NAME, PUBLIC_NAME, [(0x8001, b"\x00")])
    try:
        EchConfig.decode(mandatory.encode())
        raise AssertionError("未知强制扩展必须拒绝整条配置")
    except EchError:
        checks += 1
    return checks


# ------------------------------------------------------------ B 类：填充公式

def check_padding() -> int:
    checks = 0
    rnd, sid = b"\x33" * 32, b"\x44" * 32

    inner = make_inner(rnd, sid)
    assert name_padding(inner, MAX_NAME) == MAX_NAME - len(SNI)
    assert name_padding(inner, len(SNI)) == 0         # M == D → 不补
    assert name_padding(inner, len(SNI) - 1) == 0     # 名字比 M 长 → 补 0 而不是负数
    checks += 3

    # 没有 SNI：补 M + 9（= 一个 M 字节名字的 server_name 扩展长度）
    no_sni = ClientHello(rnd, sid, (0x1301,), b"\x00",
                         [(0x00FF, b""), (0xFE0D, ech_inner_extension())])
    assert name_padding(no_sni, MAX_NAME) == MAX_NAME + 9
    checks += 1
    # name_type 不是 host_name(0) 必须报错
    bad = ClientHello(rnd, sid, (0x1301,), b"\x00",
                      [(EXT_SERVER_NAME, vec16(b"\x01" + vec16(SNI)))])
    try:
        name_padding(bad, MAX_NAME)
        raise AssertionError("name_type 必须是 host_name")
    except EchError:
        checks += 1

    for length in (1, 2, 31, 32, 33, 63, 64, 65, 100):
        total = length + round_to_32(length)
        assert total % 32 == 0 and total >= length and total - length < 32, (length, total)
    checks += 1

    # EncodedClientHelloInner 必须清空 session_id 再补零
    enc = encoded_client_hello_inner(inner, 7)
    assert enc[-7:] == b"\x00" * 7
    assert ClientHello.decode(enc[:-7]).session_id == b""
    checks += 2
    return checks


def main() -> int:
    from ech_flow_test import (check_confirmation, check_outer_extensions,
                               check_roundtrip, check_tamper_and_wrong_key)
    groups = (
        ("RFC 9180 附录 A.2 官方向量", check_hpke_vectors),
        ("ClientHello / ECHConfig 编解码", check_codec),
        ("填充公式与 32 字节对齐", check_padding),
        ("端到端往返", check_roundtrip),
        ("篡改与错密钥必然失败", check_tamper_and_wrong_key),
        ("ech_outer_extensions 压缩与 4 条 abort", check_outer_extensions),
        ("接受确认值", check_confirmation),
    )
    total = 0
    for name, fn in groups:
        got = fn()
        total += got
        print(f"  {name}: {got} checks")
    print(f"ech_test: {total} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
