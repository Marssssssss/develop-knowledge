#!/usr/bin/env python3
"""demo573 自检：PAE、DSSE 信封、ECDSA P-256 官方向量、cosign 与 Sigstore bundle。

    python selfcheck_dsse.py
"""

import base64
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    BUNDLE_MEDIA_TYPE_CURRENT, COSIGN_CHAIN_ANNOTATION, COSIGN_SIG_ANNOTATION,
    DEPRECATED_PUBLIC_KEY_DETAILS, DSSE_VECTOR, PUBLIC_KEY_DETAILS,
    SIMPLE_SIGNING_MEDIA_TYPE, SIMPLE_SIGNING_TYPE, accept_bundle_media_type,
    build_envelope, canonicalized_body_must_match, dsse_sign, dsse_verify,
    dsse_verify_threshold, filter_keys_by_keyid, keyid_of, pae, parse_envelope,
    sha256_hex, sig_tag_for_digest, simple_signing_payload, trust_integrated_time,
    validate_bundle, DsseError,
)
import p256  # noqa: E402

PASS = 0
FAILS = []


def ok(cond, msg):
    global PASS
    if cond:
        PASS += 1
    else:
        FAILS.append(msg)
        print("FAIL: %s" % msg)


V = DSSE_VECTOR
PUB = (V["X"], V["Y"])
D = V["d"]

# ==========================================================================
# A. PAE（Pre-Authentication Encoding）
# ==========================================================================

# A1 官方测试向量逐字节对拍
ok(pae(V["payloadType"], V["payload"]) == V["pae"],
   "A1 PAE 应与官方向量一致, 实际 %r" % pae(V["payloadType"], V["payload"]))

# A2 LEN 是字节长度而不是字符数
ok(pae("é", b"x") == "DSSEv1 2 é 1 x".encode("utf-8"),
   "A2 非 ASCII 的 payloadType 按 UTF-8 字节数计数, 实际 %r" % pae("é", b"x"))

# A3 空 body 也要写出 LEN 0
ok(pae("t", b"") == b"DSSEv1 1 t 0 ",
   "A3 空 body 的 PAE 以 '0 ' 结尾, 实际 %r" % pae("t", b""))

# A4 分隔符是 ASCII 空格 0x20
ok(V["pae"].split(b" ")[0] == b"DSSEv1", "A4 PAE 以 DSSEv1 开头")
ok(pae("t", b"x").count(b" ") == 4,
   "A4b 恰好 4 个空格分隔, 实际 %d（官方向量的 payload 本身含空格，故此处用 t/x）"
   % pae("t", b"x").count(b" "))

# ==========================================================================
# B. ECDSA P-256：与官方向量对拍
# ==========================================================================

# B0 私钥推导出的公钥必须等于官方给出的 X/Y
ok(p256.point_mul(p256.G, D) == PUB,
   "B0 d·G 应等于官方公钥, 实际 %r" % (p256.point_mul(p256.G, D),))

# B1 官方签名在官方 PAE 上验签通过
want = base64.b64decode(V["sig_b64"])
wr, ws = int.from_bytes(want[:32], "big"), int.from_bytes(want[32:], "big")
ok(p256.verify(PUB, V["pae"], wr, ws), "B1 官方签名应验签通过")

# B2 改一个字节的 payload → 验签失败
bad_pae = pae(V["payloadType"], b"hello world!")
ok(not p256.verify(PUB, bad_pae, wr, ws), "B2 payload 改动后应验签失败")

# B3 改 payloadType → 验签失败（payloadType 是被认证的一部分）
ok(not p256.verify(PUB, pae("http://example.com/GoodbyeWorld", V["payload"]), wr, ws),
   "B3 payloadType 改动后应验签失败")

# B4 改签名的 s → 验签失败
ok(not p256.verify(PUB, V["pae"], wr, (ws + 1) % p256.N), "B4 改动 s 后应验签失败")

# B5 越界的 r/s 直接拒绝
ok(not p256.verify(PUB, V["pae"], 0, ws), "B5 r=0 应被拒")
ok(not p256.verify(PUB, V["pae"], wr, p256.N), "B5b s=n 应被拒")

# ==========================================================================
# C. RFC 6979 确定性签名：复现官方向量
# ==========================================================================

r, s = p256.sign(D, V["pae"])
got = base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode("ascii")
ok(got == V["sig_b64"], "C1 RFC6979 签名应逐字节复现官方向量\n   got  %s\n   want %s" % (got, V["sig_b64"]))

r2, s2 = p256.sign(D, V["pae"])
ok((r, s) == (r2, s2), "C2 同一输入的签名必须是确定性的")
ok(p256.verify(PUB, V["pae"], r, s), "C3 自己签的能通过验签")

# C4 换一个消息 → 签名不同（nonce 不是常数）
r3, _ = p256.sign(D, pae("other/type", V["payload"]))
ok(r3 != r, "C4 换消息后 nonce 必须不同，签名也应不同")

# ==========================================================================
# D. DSSE 信封的解析规则
# ==========================================================================

env = build_envelope(V["payload"], V["payloadType"], [{"sig": V["sig_b64"]}])
ok(dsse_verify(PUB, env), "D1 官方信封应验签通过")

# D2 缺 required 字段必须报错
for missing in ("payload", "payloadType", "signatures"):
    broken = {k: v for k, v in env.items() if k != missing}
    try:
        parse_envelope(broken)
        ok(False, "D2 缺 %s 应抛 DsseError" % missing)
    except DsseError:
        ok(True, "D2 缺 %s 抛 DsseError" % missing)

try:
    parse_envelope({"payload": "", "payloadType": "t", "signatures": [{}]})
    ok(False, "D3 signature 缺 sig 应抛 DsseError")
except DsseError:
    ok(True, "D3 signature 缺 sig 抛 DsseError")

# D4 keyid 的 unset 与 set-but-empty 等价
ok(keyid_of({"sig": "x"}) == keyid_of({"sig": "x", "keyid": ""}),
   "D4 keyid 缺省与空串等价")
ok(keyid_of({"sig": "x", "keyid": None}) == "",
   "D4b keyid 显式为 null 也等价为空串")

# D5 keyid 只能缩小候选范围：不匹配的 keyid 会过滤掉真实签名
env_kid = build_envelope(V["payload"], V["payloadType"],
                         [{"sig": V["sig_b64"], "keyid": "key-1"}])
ok(len(filter_keys_by_keyid(env_kid["signatures"], "key-1")) == 1,
   "D5 keyid 命中时应留下该签名")
ok(len(filter_keys_by_keyid(env_kid["signatures"], "key-2")) == 0,
   "D5b keyid 不命中时应被过滤掉")
# 但验签本身不看 keyid：即使 keyid 与公钥无关，验签仍然通过
ok(dsse_verify(PUB, env_kid), "D5c 验签不依赖 keyid（keyid 不参与安全决策）")

# D6 未识别字段必须被忽略（解析不报错，也不影响验签）
env_extra = dict(env)
env_extra["futureField"] = {"whatever": 1}
ok(dsse_verify(PUB, env_extra), "D6 未识别字段必须被忽略")

# ==========================================================================
# E. (t, n) 多签
# ==========================================================================

d2 = (D + 1) % p256.N
pub2 = p256.point_mul(p256.G, d2)
sig1 = dsse_sign(D, V["payload"], V["payloadType"])
sig2 = dsse_sign(d2, V["payload"], V["payloadType"])
pubs = {"k1": PUB, "k2": pub2}

two = build_envelope(V["payload"], V["payloadType"], [sig1, sig2])
ok(dsse_verify_threshold(pubs, two, 2), "E1 两个不同密钥满足 t=2")
ok(dsse_verify_threshold(pubs, two, 1), "E1b t=1 也满足")

# E2 同一密钥出现两次只能算 1 个（ACCEPTED_KEYS 是「unique public keys」）
dup = build_envelope(V["payload"], V["payloadType"], [sig1, dict(sig1)])
ok(not dsse_verify_threshold(pubs, dup, 2), "E2 重复密钥不应凑够 t=2")
ok(dsse_verify_threshold(pubs, dup, 1), "E2b 但 t=1 时通过")

# E3 假签名不计入
forged = {"sig": base64.b64encode(b"\x01" * 64).decode("ascii")}
mixed = build_envelope(V["payload"], V["payloadType"], [sig1, forged])
ok(not dsse_verify_threshold(pubs, mixed, 2), "E3 伪造签名不应凑够 t=2")

# ==========================================================================
# F. cosign 存储约定
# ==========================================================================

# F1 tag-based discovery
dig = "sha256:97fc222cee7991b5b061d4d4afdb5f3428fcb0c9054e1690313786befa1e4e36"
ok(sig_tag_for_digest(dig) == "sha256-97fc222cee7991b5b061d4d4afdb5f3428fcb0c9054e1690313786befa1e4e36.sig",
   "F1 digest->tag 映射, 实际 %s" % sig_tag_for_digest(dig))

# F2 常量
ok(COSIGN_SIG_ANNOTATION == "dev.cosignproject.cosign/signature", "F2 签名注解键")
ok(COSIGN_CHAIN_ANNOTATION == "dev.cosignproject.cosign/chain", "F2b 证书链注解键")
ok(SIMPLE_SIGNING_MEDIA_TYPE == "application/vnd.dev.cosign.simplesigning.v1+json",
   "F2c simple signing media type")
ok(SIMPLE_SIGNING_TYPE == "cosign container image signature", "F2d critical.type")

# F3 两跳哈希：Sign(sha256(SimpleSigningPayload(sha256(ImageManifest))))
manifest = b'{"layers":[]}'
m_digest = "sha256:" + sha256_hex(manifest)
payload_bytes = simple_signing_payload("testing/manifest", m_digest, {"creator": "atomic"})
ok(m_digest == "sha256:" + hashlib.sha256(manifest).hexdigest(), "F3 第一跳是 manifest 的 sha256")
ok(b'"Docker-manifest-digest":"sha256:' in payload_bytes or b'"Docker-manifest-digest": "sha256:' in payload_bytes,
   "F3b 载荷里嵌的是 manifest 摘要")
ok(sha256_hex(payload_bytes) != m_digest[len("sha256:"):],
   "F3c 第二跳摘要不等于第一跳（是载荷的摘要而非镜像的）")

# F4 载荷摘要可作为 blob 的 digest 被内容寻址引用
ok(len(sha256_hex(payload_bytes)) == 64, "F4 载荷摘要是 64 位十六进制")

# ==========================================================================
# G. Sigstore bundle
# ==========================================================================

# G1 media type
ok(accept_bundle_media_type(BUNDLE_MEDIA_TYPE_CURRENT), "G1 现行 v0.3+json 应被接受")
for mt in ("application/vnd.dev.sigstore.bundle+json;version=0.1",
           "application/vnd.dev.sigstore.bundle+json;version=0.2",
           "application/vnd.dev.sigstore.bundle+json;version=0.3"):
    ok(accept_bundle_media_type(mt), "G1b 旧写法 %s 应被接受" % mt)
ok(not accept_bundle_media_type("application/vnd.dev.sigstore.bundle.v0.4+json"),
   "G1c 未知版本应被拒绝")

# G2 bundle 内的 DSSE envelope 必须恰好一个签名
one = {"mediaType": BUNDLE_MEDIA_TYPE_CURRENT,
       "verificationMaterial": {},
       "dsseEnvelope": {"signatures": [{"sig": V["sig_b64"]}]}}
ok(validate_bundle(one) == [], "G2 单签名合法, 实际 %s" % validate_bundle(one))

zero = {"mediaType": BUNDLE_MEDIA_TYPE_CURRENT, "verificationMaterial": {},
        "dsseEnvelope": {"signatures": []}}
ok(any("恰好一个签名" in e for e in validate_bundle(zero)), "G2b 零签名应被拒")

two_sigs = {"mediaType": BUNDLE_MEDIA_TYPE_CURRENT, "verificationMaterial": {},
            "dsseEnvelope": {"signatures": [{"sig": V["sig_b64"]}, {"sig": V["sig_b64"]}]}}
ok(any("恰好一个签名" in e for e in validate_bundle(two_sigs)),
   "G2c 两个签名也应被拒（DSSE 允许多签，bundle 不允许）")

# G3 key hint 两处必须一致
mismatch = {"mediaType": BUNDLE_MEDIA_TYPE_CURRENT,
            "verificationMaterial": {"publicKey": {"hint": "hint-A"}},
            "dsseEnvelope": {"signatures": [{"sig": V["sig_b64"], "keyid": "hint-B"}]}}
ok(any("key hint" in e for e in validate_bundle(mismatch)), "G3 key hint 不一致应被拒")

match = {"mediaType": BUNDLE_MEDIA_TYPE_CURRENT,
         "verificationMaterial": {"publicKey": {"hint": "hint-A"}},
         "dsseEnvelope": {"signatures": [{"sig": V["sig_b64"], "keyid": "hint-A"}]}}
ok(validate_bundle(match) == [], "G3b key hint 一致时通过, 实际 %s" % validate_bundle(match))

# G4 verificationMaterial 是 REQUIRED
no_vm = {"mediaType": BUNDLE_MEDIA_TYPE_CURRENT}
ok(any("verificationMaterial" in e for e in validate_bundle(no_vm)),
   "G4 缺 verificationMaterial 应被拒")

# G5 integrated_time 的可信性
ok(trust_integrated_time({"inclusionPromise": {}, "integratedTime": 1}) is True,
   "G5 有 inclusion_promise 时 integrated_time 可信任")
ok(trust_integrated_time({"integratedTime": 1}) is False,
   "G5b 缺 inclusion_promise 时 MUST NOT be trusted")

# G6 canonicalized_body
ok(canonicalized_body_must_match({}, "sig") is None,
   "G6 未设置 canonicalized_body 时不做此校验")
ok(canonicalized_body_must_match({"canonicalizedBody": {"sig": "abc"}}, "abc") is True,
   "G6b 签名一致时通过")
ok(canonicalized_body_must_match({"canonicalizedBody": {"sig": "abc"}}, "xyz") is False,
   "G6c 签名不一致时应被拒")

# ==========================================================================
# H. PublicKeyDetails 枚举
# ==========================================================================

ok(PUBLIC_KEY_DETAILS[5] == "PKIX_ECDSA_P256_SHA_256", "H1 现行 ECDSA P-256 是 5")
ok(6 in DEPRECATED_PUBLIC_KEY_DETAILS, "H2 RFC6979 那一种（6）已废弃")
ok(PUBLIC_KEY_DETAILS[6] == "PKIX_ECDSA_P256_HMAC_SHA_256", "H2b 废弃项的名字")

# ==========================================================================
# I. 端到端：签一个 in-toto 风格的证明并验回来
# ==========================================================================

stmt = b'{"_type":"https://in-toto.io/Statement/v1","subject":[{"name":"img","digest":{"sha256":"aa"}}]}'
ptype = "application/vnd.in-toto+json"
sig = dsse_sign(D, stmt, ptype, keyid="my-key")
e2e = build_envelope(stmt, ptype, [sig])
ok(dsse_verify(PUB, e2e), "I1 端到端验签通过")
# 换成 DSSE 之外的 payloadType 立刻失败（payloadType 参与签名）
tampered = dict(e2e)
tampered["payloadType"] = "application/vnd.in-toto+json "
ok(not dsse_verify(PUB, tampered), "I2 改 payloadType 后验签失败")
# payload 也必须原样（不能验完再重新解析信封取 payload）
tampered2 = dict(e2e)
tampered2["payload"] = base64.b64encode(stmt + b" ").decode("ascii")
ok(not dsse_verify(PUB, tampered2), "I3 改 payload 后验签失败")

print("PASS=%d" % PASS)
if FAILS:
    print("FAILED=%d" % len(FAILS))
    sys.exit(1)
print("ALL OK")
