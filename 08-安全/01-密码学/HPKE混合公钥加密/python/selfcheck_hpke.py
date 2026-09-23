"""HPKE 自检：以 RFC 9180 附录 A.1 的官方向量为准。

跑法：`python selfcheck_hpke.py`
"""

import hpke as H
import hpke_aes as A
import hpke_x25519 as X

TOTAL = [0]
FAILED = []


def check(label, cond, detail=""):
    TOTAL[0] += 1
    if not cond:
        FAILED.append((label, detail))
        print(f"  FAIL {label}: {detail}")


def summary():
    print(f"\n断言 {TOTAL[0]} 条，失败 {len(FAILED)} 条")
    for label, detail in FAILED:
        print(f"  - {label}: {detail}")
    return 1 if FAILED else 0


# ------------------------------------------------- RFC 9180 A.1.1（Base）
SKE = bytes.fromhex("52c4a758a802cd8b936eceea314432798d5baf2d7e9235dc084ab1b9cfa2f736")
PKE = bytes.fromhex("37fda3567bdbd628e88668c3c8d7e97d1d1253b6d4ea6d44c150f741f1bf4431")
SKR = bytes.fromhex("4612c550263fc8ad58375df3f557aac531d26850903e55a9f23f21d8534e8ac8")
PKR = bytes.fromhex("3948cfe0ad1ddb695d780e59077195da6c56506b027329794ab02bca80815c4d")
INFO = bytes.fromhex("4f6465206f6e2061204772656369616e2055726e")
PT = bytes.fromhex("4265617574792069732074727574682c20747275746820626561757479")

VECTORS = [
    (0, "56d890e5accaaf011cff4b7d", "436f756e742d30",
     "f938558b5d72f1a23810b4be2ab4f84331acc02fc97babc53a52ae8218a355a9"
     "6d8770ac83d07bea87e13c512a"),
    (1, "56d890e5accaaf011cff4b7c", "436f756e742d31",
     "af2d7e9ac9ae7e270f46ba1f975be53c09f8d875bdc8535458c2494e8a6eab25"
     "1c03d0c22a56b8ca42c2063b84"),
    (2, "56d890e5accaaf011cff4b7f", "436f756e742d32",
     "498dfcabd92e8acedc281e85af1cb4e3e31c7dc394a1ca20e173cb7251649158"
     "8d96a19ad4a683518973dcc180"),
    (4, "56d890e5accaaf011cff4b79", "436f756e742d34",
     "583bd32bc67a5994bb8ceaca813d369bca7b2a42408cddef5e22f880b631215a"
     "09fc0012bc69fccaa251c0246d"),
    (255, "56d890e5accaaf011cff4b82", "436f756e742d323535",
     "7175db9717964058640a3a11fb9007941a5d1757fda1a6935c805c21af32505b"
     "f106deefec4a49ac38d71c9e0a"),
    (256, "56d890e5accaaf011cff4a7d", "436f756e742d323536",
     "957f9800542b0b8891badb026d79cc54597cb2d225b54c00c5238c25d05c30e3"
     "fbeda97d2e0e1aba483a2df9f2"),
]

EXPORT_VECTORS = [
    ("", "3853fe2b4035195a573ffc53856e77058e15d9ea064de3e59f4961d0095250ee"),
    ("00", "2e8f0b54673c7029649d4eb9d5e33bf1872cf76d623ff164ac185da9e88c21a5"),
    ("54657374436f6e74657874",
     "e9e43065102c3836401bed8c3c3c75ae46be1639869391d62c61f1ec7af54931"),
]


def test_x25519():
    check("X25519(skE, G) == pkEm", X.x25519_base(SKE) == PKE)
    check("X25519(skR, G) == pkRm", X.x25519_base(SKR) == PKR)
    check("X25519 交换律：X(skE, pkR) == X(skR, pkE)",
          X.x25519(SKE, PKR) == X.x25519(SKR, PKE))
    k = bytearray(SKE)
    clamped = X.clamp_scalar(SKE)
    check("clamp 清低 3 位", clamped[0] & 7 == 0)
    check("clamp 清最高位", clamped[31] & 0x80 == 0)
    check("clamp 置次高位", clamped[31] & 0x40 == 0x40)
    check("clamp 幂等", X.clamp_scalar(clamped) == clamped)
    check("u 坐标最高位被屏蔽", X.decode_u(b"\xff" * 32) == (1 << 255) - 1)


def test_hkdf():
    # RFC 5869 A.1
    ikm = bytes.fromhex("0b" * 22)
    salt = bytes.fromhex("000102030405060708090a0b0c")
    info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    prk = X.hkdf_extract(salt, ikm)
    check("HKDF Extract 对上 RFC 5869 A.1",
          prk.hex() == "077709362c2e32df0ddc3f0dc47bba6390b6c73bb50f9c3122ec844ad7c2b3e5", prk.hex())
    okm = X.hkdf_expand(prk, info, 42)
    check("HKDF Expand 对上 RFC 5869 A.1",
          okm.hex() == "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
                       "34007208d5b887185865", okm.hex())


def test_suite_ids():
    check("KEM suite_id = 'KEM' || I2OSP(32, 2)",
          H.KEM_SUITE_ID == b"KEM\x00\x20", H.KEM_SUITE_ID.hex())
    check("HPKE suite_id = 'HPKE' || kem || kdf || aead",
          H.HPKE_SUITE_ID == b"HPKE\x00\x20\x00\x01\x00\x01", H.HPKE_SUITE_ID.hex())


def test_base_setup():
    ss, enc = H.encap(PKR, SKE)
    check("enc == pkEm（封装出来的就是临时公钥）", enc == PKE)
    check("shared_secret 对上 A.1.1",
          ss.hex() == "fe0e18c9f024ce43799ae393c7e8fe8fce9d218875e8227b0187c04e7d2ea1fc", ss.hex())
    check("Decap 与 Encap 得到同一个 shared_secret", H.decap(enc, SKR) == ss)
    ks = H.key_schedule(H.MODE_BASE, ss, INFO)
    check("key_schedule_context 对上 A.1.1",
          ks["key_schedule_context"].hex() ==
          "00725611c9d98c07c03f60095cd32d400d8347d45ed67097bbad50fc56da742d07"
          "cb6cffde367bb0565ba28bb02c90744a20f5ef37f30523526106f637abb05449")
    check("secret 对上 A.1.1",
          ks["secret"].hex() ==
          "12fff91991e93b48de37e7daddb52981084bd8aa64289c3788471d9a9712f397")
    check("key 对上 A.1.1", ks["key"].hex() == "4531685d41d65f03dc48f6b8302c05b0")
    check("base_nonce 对上 A.1.1",
          ks["base_nonce"].hex() == "56d890e5accaaf011cff4b7d")
    check("exporter_secret 对上 A.1.1",
          ks["exporter_secret"].hex() ==
          "45ff1c2e220db587171952c0592d5f5ebe103f1561a2614e38f2ffd47e99e3f8")


def test_aes_gcm():
    rk = A.key_expansion(bytes.fromhex("000102030405060708090a0b0c0d0e0f"))
    ct = A.encrypt_block(rk, bytes.fromhex("00112233445566778899aabbccddeeff"))
    check("AES-128 对上 FIPS 197 C.1",
          ct.hex() == "69c4e0d86a7b0430d8cdb78070b4c55a", ct.hex())
    g = A.AES128GCM(bytes(16))
    check("GCM 空明文对上经典向量",
          g.seal(bytes(12), b"", b"").hex() == "58e2fccefa7e3061367f1d57a4e7455a")
    check("GCM 单块对上经典向量",
          g.seal(bytes(12), b"", bytes(16)).hex() ==
          "0388dace60b6a392f328c2b971b2fe78ab6e47d42cec13bdf53a67b21257bddf")
    check("GCM 篡改密文会被拒", _raises(lambda: g.open(bytes(12), b"",
                                                  bytes(16) + b"\x00" * 15 + b"\x01")))


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


def test_encryptions():
    """按 RFC 的做法：从 seq 0 一路 Seal 到 256，逐条比对密文。"""
    ss, enc = H.encap(PKR, SKE)
    ks = H.key_schedule(H.MODE_BASE, ss, INFO)
    ctx_s = H.ContextS(ks)
    for seq, nonce_hex, aad_hex, ct_hex in VECTORS:
        while ctx_s.seq < seq:
            ctx_s.increment_seq()
        got = ctx_s.seal(bytes.fromhex(aad_hex), PT)
        check(f"seq {seq} 的 nonce = {nonce_hex}",
              ctx_s.compute_nonce(seq).hex() == nonce_hex,
              ctx_s.compute_nonce(seq).hex())
        check(f"seq {seq} 的密文对上 A.1.1.1", got.hex() == ct_hex, got.hex())
        ctx_s.seq = seq  # 上面 seal 已自增，复位以便下一条继续


def test_decrypt_roundtrip():
    ss, enc = H.encap(PKR, SKE)
    ctx_r = H.ContextR(H.key_schedule(H.MODE_BASE, ss, INFO))
    ss2, enc2 = H.encap(PKR, SKE)
    ctx_s = H.ContextS(H.key_schedule(H.MODE_BASE, ss2, INFO))
    c0 = ctx_s.seal(b"hdr", PT)
    c1 = ctx_s.seal(b"hdr", PT)
    check("接收端按序解开第 0 条", ctx_r.open(b"hdr", c0) == PT)
    check("接收端按序解开第 1 条", ctx_r.open(b"hdr", c1) == PT)
    check("seq 已推进到 2", ctx_r.seq == 2)
    # 乱序会解不开（nonce 与 seq 绑定）
    ctx_r2 = H.ContextR(H.key_schedule(H.MODE_BASE, ss, INFO))
    ctx_r2.increment_seq()
    check("负控：接收端 seq 错位时解不开第一条",
          _raises(lambda: ctx_r2.open(b"hdr", c0)))
    # AAD 不匹配
    ctx_r3 = H.ContextR(H.key_schedule(H.MODE_BASE, ss, INFO))
    check("负控：AAD 不同时解不开", _raises(lambda: ctx_r3.open(b"other", c0)))


def test_sequencing():
    ss, _ = H.encap(PKR, SKE)
    ctx = H.ContextS(H.key_schedule(H.MODE_BASE, ss, INFO))
    check("nonce = base_nonce XOR I2OSP(seq, 12)",
          ctx.compute_nonce(0).hex() == "56d890e5accaaf011cff4b7d")
    check("seq=1 只翻转最低字节",
          ctx.compute_nonce(1).hex() == "56d890e5accaaf011cff4b7c")
    check("seq=256 翻的是倒数第二字节（不是最低字节）",
          ctx.compute_nonce(256).hex() == "56d890e5accaaf011cff4a7d")
    ctx.seq = (1 << 96) - 1
    check("seq 到达 2^96-1 后 increment 抛 MessageLimitReached",
          _raises(ctx.increment_seq))
    # 发送/接收角色不可互换
    ss2, _ = H.encap(PKR, SKE)
    s = H.ContextS(H.key_schedule(H.MODE_BASE, ss2, INFO))
    check("负控：发送方上下文不能用于解密", _raises(lambda: s.open(b"", b"\x00" * 16)))


def test_export():
    ss, _ = H.encap(PKR, SKE)
    ctx = H.ContextS(H.key_schedule(H.MODE_BASE, ss, INFO))
    for ctx_hex, exp in EXPORT_VECTORS:
        got = ctx.export(bytes.fromhex(ctx_hex), 32).hex()
        check(f"Export(context={ctx_hex or 'empty'}) 对上 A.1.1.2", got == exp, got)
    check("Export 不消耗 seq（导出前后 seq 不变）",
          (ctx.export(b"", 32), ctx.seq)[1] == 0)


def test_psk_modes():
    ss, _ = H.encap(PKR, SKE)
    psk = bytes.fromhex("0247fd33b913760fa1fa51e1892d9f307fbe65eb171e8132c2af18555a738b82")
    psk_id = bytes.fromhex("456e6e796e20447572696e206172616e204d6f726961")
    check("Base 模式带 PSK 会被 VerifyPSKInputs 拒绝",
          _raises(lambda: H.key_schedule(H.MODE_BASE, ss, INFO, psk, psk_id)))
    check("PSK 模式缺 PSK 会被拒绝",
          _raises(lambda: H.key_schedule(H.MODE_PSK, ss, INFO)))
    check("只给 psk 不给 psk_id 会被拒绝",
          _raises(lambda: H.key_schedule(H.MODE_PSK, ss, INFO, psk, H.DEFAULT_PSK_ID)))
    ks = H.key_schedule(H.MODE_PSK, ss, INFO, psk, psk_id)
    check("PSK 模式能正常派生", len(ks["key"]) == 16 and len(ks["base_nonce"]) == 12)
    ks_base = H.key_schedule(H.MODE_BASE, ss, INFO)
    check("PSK 模式的 key 与 Base 模式不同（psk 进了 secret 的 ikm）",
          ks["key"] != ks_base["key"])


def main():
    test_x25519()
    test_hkdf()
    test_suite_ids()
    test_aes_gcm()
    test_base_setup()
    test_encryptions()
    test_decrypt_roundtrip()
    test_sequencing()
    test_export()
    test_psk_modes()
    return summary()


if __name__ == "__main__":
    raise SystemExit(main())
