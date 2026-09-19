# -*- coding: utf-8 -*-
"""TLS 1.3 密钥调度（RFC 8446 §7.1-§7.3）原理级实现。

从零实现：SHA-256(FIPS 180-4) → HMAC(RFC 2104) → HKDF Extract/Expand(RFC 5869)
→ HKDF-Expand-Label / Derive-Secret → 五层 Secret 调度 → traffic key/iv/nonce。

关键结构（RFC 8446 §7.1）：
    HKDF-Expand-Label(Secret, Label, Context, Length) =
        HKDF-Expand(Secret, HkdfLabel, Length)
    struct {
        uint16 length = Length;
        opaque label<7..255>   = "tls13 " + Label;
        opaque context<0..255> = Context;
    } HkdfLabel;
    Derive-Secret(Secret, Label, Messages) =
        HKDF-Expand-Label(Secret, Label, Transcript-Hash(Messages), Hash.length)
"""

HASH_LEN = 32  # TLS_AES_128_GCM_SHA256 / TLS_CHACHA20_POLY1305_SHA256 的哈希长度

# ---------------------------------------------------------------- SHA-256
_K = [
    0x428A2F98, 0x71374491, 0xB5C0FBCF, 0xE9B5DBA5, 0x3956C25B, 0x59F111F1,
    0x923F82A4, 0xAB1C5ED5, 0xD807AA98, 0x12835B01, 0x243185BE, 0x550C7DC3,
    0x72BE5D74, 0x80DEB1FE, 0x9BDC06A7, 0xC19BF174, 0xE49B69C1, 0xEFBE4786,
    0x0FC19DC6, 0x240CA1CC, 0x2DE92C6F, 0x4A7484AA, 0x5CB0A9DC, 0x76F988DA,
    0x983E5152, 0xA831C66D, 0xB00327C8, 0xBF597FC7, 0xC6E00BF3, 0xD5A79147,
    0x06CA6351, 0x14292967, 0x27B70A85, 0x2E1B2138, 0x4D2C6DFC, 0x53380D13,
    0x650A7354, 0x766A0ABB, 0x81C2C92E, 0x92722C85, 0xA2BFE8A1, 0xA81A664B,
    0xC24B8B70, 0xC76C51A3, 0xD192E819, 0xD6990624, 0xF40E3585, 0x106AA070,
    0x19A4C116, 0x1E376C08, 0x2748774C, 0x34B0BCB5, 0x391C0CB3, 0x4ED8AA4A,
    0x5B9CCA4F, 0x682E6FF3, 0x748F82EE, 0x78A5636F, 0x84C87814, 0x8CC70208,
    0x90BEFFFA, 0xA4506CEB, 0xBEF9A3F7, 0xC67178F2]


def _rotr(x, n):
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF


def sha256(data):
    h = [0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
         0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19]
    ml = len(data) * 8
    m = bytearray(data) + b"\x80"
    m += b"\x00" * ((56 - len(m) % 64) % 64) + ml.to_bytes(8, "big")
    w = [0] * 64
    for off in range(0, len(m), 64):
        for i in range(16):
            w[i] = int.from_bytes(m[off + 4 * i:off + 4 * i + 4], "big")
        for i in range(16, 64):
            s0 = _rotr(w[i - 15], 7) ^ _rotr(w[i - 15], 18) ^ (w[i - 15] >> 3)
            s1 = _rotr(w[i - 2], 17) ^ _rotr(w[i - 2], 19) ^ (w[i - 2] >> 10)
            w[i] = (w[i - 16] + s0 + w[i - 7] + s1) & 0xFFFFFFFF
        a, b, c, d, e, f, g, hh = h
        for i in range(64):
            s1 = _rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25)
            ch = (e & f) ^ (~e & g)
            t1 = (hh + s1 + ch + _K[i] + w[i]) & 0xFFFFFFFF
            s0 = _rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22)
            mj = (a & b) ^ (a & c) ^ (b & c)
            t2 = (s0 + mj) & 0xFFFFFFFF
            hh, g, f, e, d, c, b, a = g, f, e, (d + t1) & 0xFFFFFFFF, c, b, a, (t1 + t2) & 0xFFFFFFFF
        h = [(x + y) & 0xFFFFFFFF for x, y in zip(h, [a, b, c, d, e, f, g, hh])]
    return b"".join(x.to_bytes(4, "big") for x in h)


# ---------------------------------------------------------------- HMAC / HKDF
def hmac_sha256(key, msg):
    blk = 64
    if len(key) > blk:
        key = sha256(key)
    key = key + b"\x00" * (blk - len(key))
    ipad = bytes(b ^ 0x36 for b in key)
    opad = bytes(b ^ 0x5C for b in key)
    return sha256(opad + sha256(ipad + msg))


def hkdf_extract(salt, ikm):
    return hmac_sha256(salt, ikm)


def hkdf_expand(prk, info, length):
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac_sha256(prk, t + info + bytes([i]))
        out += t
        i += 1
    return out[:length]


# ---------------------------------------------------------------- TLS 1.3 标签
def hkdf_label(length, label, context):
    """HkdfLabel 的字节编码：uint16 length || len(label) || label || len(ctx) || ctx"""
    lab = b"tls13 " + label.encode()
    return (length.to_bytes(2, "big") + bytes([len(lab)]) + lab
            + bytes([len(context)]) + context)


def expand_label(secret, label, context, length):
    return hkdf_expand(secret, hkdf_label(length, label, context), length)


def derive_secret(secret, label, transcript_hash):
    """Derive-Secret：Context 恒为 Transcript-Hash，长度恒为 Hash.length"""
    return expand_label(secret, label, transcript_hash, HASH_LEN)


ZERO = b"\x00" * HASH_LEN


def transcript(*msgs):
    """Transcript-Hash(Messages)：握手消息（含类型与长度字段）拼接后再哈希"""
    return sha256(b"".join(msgs))


def key_schedule(psk, dhe, ch_sh=None, ch=None, sh=None, sf=None, cf=None):
    """返回 RFC 8446 §7.1 图里的全部 Secret。psk/dhe 为 None 时按 0 值代入。"""
    psk = psk if psk is not None else ZERO
    dhe = dhe if dhe is not None else ZERO
    hs_ctx = ch_sh if ch_sh is not None else transcript(ch, sh)   # CH..ServerHello
    ap_ctx = transcript(ch, sh, sf)                               # CH..server Finished
    out = {}
    out["early_secret"] = hkdf_extract(ZERO, psk)
    es = out["early_secret"]
    out["binder_key_res"] = derive_secret(es, "res binder", b"")
    out["binder_key_ext"] = derive_secret(es, "ext binder", b"")
    out["client_early_traffic"] = derive_secret(es, "c e traffic", transcript(ch))
    out["early_exporter_master"] = derive_secret(es, "e exp master", transcript(ch))
    out["derived_es"] = derive_secret(es, "derived", b"")
    out["handshake_secret"] = hkdf_extract(out["derived_es"], dhe)
    hs = out["handshake_secret"]
    out["client_handshake_traffic"] = derive_secret(hs, "c hs traffic", hs_ctx)
    out["server_handshake_traffic"] = derive_secret(hs, "s hs traffic", hs_ctx)
    out["derived_hs"] = derive_secret(hs, "derived", b"")
    out["master_secret"] = hkdf_extract(out["derived_hs"], ZERO)
    ms = out["master_secret"]
    out["client_app_traffic_0"] = derive_secret(ms, "c ap traffic", ap_ctx)
    out["server_app_traffic_0"] = derive_secret(ms, "s ap traffic", ap_ctx)
    out["exporter_master"] = derive_secret(ms, "exp master", ap_ctx)
    out["resumption_master"] = derive_secret(ms, "res master", ap_ctx)
    return out


def traffic_keys(secret, key_len, iv_len):
    """§7.3：[sender]_write_key / write_iv 都走 Expand-Label，Context 为空串"""
    return expand_label(secret, "key", b"", key_len), expand_label(secret, "iv", b"", iv_len)


def aead_nonce(iv, seq):
    """§5.3：左补零到 iv_length 的序列号与静态 IV 按位异或"""
    s = seq.to_bytes(len(iv), "big")
    return bytes(x ^ y for x, y in zip(iv, s))


def key_update(secret):
    """§7.2：application_traffic_secret_N+1 = Expand-Label(., "traffic upd", "", Hash.length)"""
    return expand_label(secret, "traffic upd", b"", HASH_LEN)


# ---------------------------------------------------------------- 自检
def _hex(b):
    return b.hex()


def selfcheck():
    ok = 0

    def chk(cond, msg):
        nonlocal ok
        assert cond, msg
        ok += 1

    # --- 底座：FIPS 180-4 SHA-256 向量 + RFC 4231 HMAC 向量 + RFC 5869 HKDF 向量
    chk(_hex(sha256(b"abc")) ==
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "SHA-256(abc)")
    chk(_hex(hmac_sha256(b"\x0b" * 20, b"Hi There")) ==
        "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7",
        "HMAC RFC 4231 #1")
    ikm = bytes.fromhex("0b" * 22)
    salt = bytes.fromhex("000102030405060708090a0b0c")
    info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    prk = hkdf_extract(salt, ikm)
    chk(_hex(prk) ==
        "077709362c2e32df0ddc3f0dc47bba6390b6c73bb50f9c3122ec844ad7c2b3e5",
        "HKDF Extract RFC 5869 A.1")
    chk(_hex(hkdf_expand(prk, info, 42)) ==
        "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865",
        "HKDF Expand RFC 5869 A.1")

    # --- HkdfLabel 编码：uint16 length + 1 字节标签长度 + "tls13 " 前缀 + 1 字节上下文长度
    lab = hkdf_label(32, "derived", b"")
    chk(lab == bytes.fromhex("0020") + bytes([13]) + b"tls13 derived" + b"\x00",
        "HkdfLabel 编码（label 恒带 tls13 前缀，空 context 也要 1 字节长度）")
    chk(hkdf_label(32, "c e traffic", sha256(b"x"))[0:2] == b"\x00\x20", "长度字段大端")
    chk(len(hkdf_label(32, "key", b"")) == 2 + 1 + 9 + 1, "key 标签 9 字节=tls13 +key")

    # --- 五层调度：未使用 PSK 时 Early Secret 仍是 HKDF-Extract(0,0)，不能跳过轮次
    ks = key_schedule(None, bytes(range(32)), ch=b"CH", sh=b"SH", sf=b"SF")
    chk(ks["early_secret"] == hkdf_extract(ZERO, ZERO), "无 PSK 时 Early Secret = Extract(0,0)")
    chk(ks["master_secret"] == hkdf_extract(derive_secret(ks["handshake_secret"], "derived", b""), ZERO),
        "Master Secret 的 IKM 是 0 值（不是复用 handshake secret）")

    # --- 标签分离：同一 Early Secret 下 ext/res binder 必须不同
    chk(ks["binder_key_ext"] != ks["binder_key_res"], "ext binder 与 res binder 标签分离")
    chk(ks["client_handshake_traffic"] != ks["server_handshake_traffic"], "c/s hs traffic 分离")
    chk(ks["client_app_traffic_0"] != ks["server_app_traffic_0"], "c/s ap traffic 分离")

    # --- 上下文绑定：Derive-Secret 把 Transcript-Hash 作为 Context 灌进去
    ks2 = key_schedule(None, bytes(range(32)), ch=b"CH2", sh=b"SH", sf=b"SF")
    chk(ks["handshake_secret"] == ks2["handshake_secret"], "Handshake Secret 在 derived 处不绑定 transcript")
    chk(ks["client_handshake_traffic"] != ks2["client_handshake_traffic"],
        "同 Secret 不同 transcript ⇒ 不同 traffic secret（密钥绑定握手上下文）")
    chk(ks["client_app_traffic_0"] != ks2["client_app_traffic_0"], "ap traffic 同样随 transcript 变化")

    # --- (EC)DHE 只从 Handshake Secret 起注入，Early Secret 不受影响
    ks3 = key_schedule(None, bytes([0x5A] * 32), ch=b"CH", sh=b"SH", sf=b"SF")
    chk(ks3["early_secret"] == ks["early_secret"], "Early Secret 与 DHE 无关")
    chk(ks3["handshake_secret"] != ks["handshake_secret"], "DHE 从 Handshake Secret 起注入")

    # --- PSK 注入点
    ks4 = key_schedule(b"\x11" * 32, bytes(range(32)), ch=b"CH", sh=b"SH", sf=b"SF")
    chk(ks4["early_secret"] != ks["early_secret"], "PSK 改变 Early Secret")
    chk(ks4["handshake_secret"] != ks["handshake_secret"], "PSK 经 derived 间接改变 Handshake Secret")

    # --- traffic key / iv / nonce（§7.3 + §5.3）
    key, iv = traffic_keys(ks["client_app_traffic_0"], 16, 12)
    chk(len(key) == 16 and len(iv) == 12, "TLS_AES_128_GCM_SHA256: key 16 / iv 12 字节")
    chk(key == expand_label(ks["client_app_traffic_0"], "key", b"", 16), "key 走 Expand-Label")
    chk(iv == expand_label(ks["client_app_traffic_0"], "iv", b"", 12), "iv 走 Expand-Label")
    chk(aead_nonce(iv, 0) == iv, "seq=0 时 nonce 等于静态 IV")
    chk(aead_nonce(iv, 1) == iv[:-1] + bytes([iv[-1] ^ 1]), "nonce = 左补零序列号 XOR IV")
    chk(aead_nonce(iv, 1)[:11] == iv[:11], "左补零只影响末尾字节")

    # --- KeyUpdate 更新链（§7.2）
    n1 = key_update(ks["client_app_traffic_0"])
    n2 = key_update(n1)
    chk(n1 != ks["client_app_traffic_0"] and n2 != n1, "每次 KeyUpdate 都换一把新 secret")
    chk(key_update(ks["client_app_traffic_0"]) == n1, "更新是确定性函数（可重放验证）")
    k1, v1 = traffic_keys(n1, 16, 12)
    chk(k1 != key and v1 != iv, "换 secret 后 key/iv 全部重算")

    # --- 0-RTT：早期流量密钥只由 Early Secret 决定，不依赖 ServerHello
    ks5 = key_schedule(b"\x11" * 32, bytes(range(32)), ch=b"CH", sh=b"SH", sf=b"SF")
    ks6 = key_schedule(b"\x11" * 32, bytes(range(32)), ch=b"CH", sh=b"SH2", sf=b"SF2")
    chk(ks5["client_early_traffic"] == ks6["client_early_traffic"],
        "0-RTT 密钥在 ClientHello 时即可算出（不依赖 ServerHello）")
    chk(ks5["client_app_traffic_0"] != ks6["client_app_traffic_0"], "应用流量密钥反之必须等握手结束")

    print(f"TLS 1.3 密钥调度自检通过：{ok} 项断言")
    return ok


if __name__ == "__main__":
    selfcheck()
