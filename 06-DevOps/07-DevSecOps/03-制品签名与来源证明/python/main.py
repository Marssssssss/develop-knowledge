#!/usr/bin/env python3
"""DSSE 信封、Sigstore bundle 与 cosign 存储约定的最小可执行模型。

三处规范来源：

* ``secure-systems-lab/dsse`` 的 protocol.md / envelope.md（PAE、信封字段、多签）
* ``sigstore/protobuf-specs`` 的 ``sigstore_bundle.proto`` / ``sigstore_rekor.proto``
* ``sigstore/cosign`` 的 ``specs/SIGNATURE_SPEC.md``（tag 发现、注解键、两跳哈希）

P-256 的 ECDSA 见 ``p256.py``。
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from p256 import N, sign as ecdsa_sign, verify as ecdsa_verify

# --------------------------------------------------------------------------
# 1. DSSE 官方测试向量（protocol.md 的 Test Vectors 一节）
# --------------------------------------------------------------------------

DSSE_VECTOR = {
    "payload": b"hello world",
    "payloadType": "http://example.com/HelloWorld",
    "pae": b"DSSEv1 29 http://example.com/HelloWorld 11 hello world",
    "X": 46950820868899156662930047687818585632848591499744589407958293238635476079160,
    "Y": 5640078356564379163099075877009565129882514886557779369047442380624545832820,
    "d": 97358161215184420915383655311931858321456579547487070936769975997791359926199,
    "sig_b64": ("A3JqsQGtVsJ2O2xqrI5IcnXip5GToJ3F+FnZ+O88SjtR6rDAajabZKciJTfUiHqJPcIAriEGA"
                "HTVeCUjW2JIZA=="),
}


# --------------------------------------------------------------------------
# 2. PAE（Pre-Authentication Encoding）
# --------------------------------------------------------------------------


def pae(payload_type: str, body: bytes) -> bytes:
    """PAE(type, body) = "DSSEv1" SP LEN(type) SP type SP LEN(body) SP body

    ``LEN(s)`` 是 **字节长度**的十进制（无前导零），不是字符数；
    ``type`` 先做 UTF-8 编码再参与拼接。
    """
    t = payload_type.encode("utf-8")
    return (b"DSSEv1" + b" " + str(len(t)).encode("ascii") + b" " + t
            + b" " + str(len(body)).encode("ascii") + b" " + body)


# --------------------------------------------------------------------------
# 3. DSSE 信封
# --------------------------------------------------------------------------

REQUIRED_FIELDS = ("payload", "payloadType", "signatures")
REQUIRED_SIG_FIELDS = ("sig",)


class DsseError(Exception):
    pass


def build_envelope(payload: bytes, payload_type: str,
                   signatures: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "payload": base64.b64encode(payload).decode("ascii"),
        "payloadType": payload_type,
        "signatures": list(signatures),
    }


def parse_envelope(raw: Dict[str, Any]) -> Tuple[bytes, str, List[Dict[str, str]]]:
    """按 envelope.md 的 Parsing rules 解析；不合法直接抛 DsseError。

    要点：``payload`` / ``payloadType`` / ``signatures`` / ``signatures[].sig``
    是必须存在（即使为空）的字段；``keyid`` 可选，且 **unset 与 set-but-empty 等价**；
    未识别字段必须忽略（本函数不校验额外字段）。
    """
    for f in REQUIRED_FIELDS:
        if f not in raw:
            raise DsseError("missing required field: %s" % f)
    sigs = raw["signatures"]
    if not isinstance(sigs, list):
        raise DsseError("signatures must be a list")
    for s in sigs:
        for f in REQUIRED_SIG_FIELDS:
            if f not in s:
                raise DsseError("missing required field: signature.%s" % f)
    try:
        payload = base64.b64decode(raw["payload"], validate=False)
    except Exception as exc:                      # pragma: no cover
        raise DsseError("payload is not valid base64: %s" % exc)
    return payload, raw["payloadType"], sigs


def keyid_of(sig: Dict[str, str]) -> str:
    """unset 与 set-but-empty 必须被同等对待。"""
    return sig.get("keyid", "") or ""


def filter_keys_by_keyid(sigs: List[Dict[str, str]], keyid: str) -> List[Dict[str, str]]:
    """DSSE 规定 keyid 只能用来**缩小候选密钥范围**，不能用于安全决策。"""
    return [s for s in sigs if keyid_of(s) == keyid]


# --------------------------------------------------------------------------
# 4. 签名与验签（PAE 之上）
# --------------------------------------------------------------------------


def dsse_sign(d: int, payload: bytes, payload_type: str,
              keyid: Optional[str] = None) -> Dict[str, str]:
    r, s = ecdsa_sign(d, pae(payload_type, payload))
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")   # 原始 r||s，不是 DER
    out = {"sig": base64.b64encode(raw).decode("ascii")}
    if keyid is not None:
        out["keyid"] = keyid
    return out


def _decode_sig(sig_b64: str) -> Tuple[int, int]:
    raw = base64.b64decode(sig_b64)
    if len(raw) != 64:
        raise DsseError("signature must be 64 bytes (raw r||s), got %d" % len(raw))
    return int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")


def dsse_verify(pub: Tuple[int, int], env: Dict[str, Any]) -> bool:
    try:
        payload, ptype, sigs = parse_envelope(env)
    except DsseError:
        return False
    for s in sigs:
        r, ss = _decode_sig(s["sig"])
        if ecdsa_verify(pub, pae(ptype, payload), r, ss):
            return True
    return False


def dsse_verify_threshold(pubs: Dict[str, Tuple[int, int]], env: Dict[str, Any],
                          t: int) -> bool:
    """(t, n) 多签：需要至少 t 个**互不相同**的受信任公钥通过验签。"""
    try:
        payload, ptype, sigs = parse_envelope(env)
    except DsseError:
        return False
    accepted: set = set()
    for s in sigs:
        r, ss = _decode_sig(s["sig"])
        for name, pub in pubs.items():
            if name in accepted:
                continue
            if ecdsa_verify(pub, pae(ptype, payload), r, ss):
                accepted.add(name)
                break
        if len(accepted) >= t:
            return True
    return len(accepted) >= t


# --------------------------------------------------------------------------
# 5. cosign 存储约定
# --------------------------------------------------------------------------

COSIGN_SIG_ANNOTATION = "dev.cosignproject.cosign/signature"
COSIGN_CERT_ANNOTATION = "dev.cosignproject.cosign/certificate"
COSIGN_CHAIN_ANNOTATION = "dev.cosignproject.cosign/chain"
SIMPLE_SIGNING_MEDIA_TYPE = "application/vnd.dev.cosign.simplesigning.v1+json"
SIMPLE_SIGNING_TYPE = "cosign container image signature"


def sig_tag_for_digest(digest: str) -> str:
    """cosign 的 tag-based discovery：把 ':' 换成 '-' 并加 '.sig' 后缀。"""
    if ":" not in digest:
        raise DsseError("digest must be of the form <alg>:<hex>")
    return digest.replace(":", "-") + ".sig"


def simple_signing_payload(docker_reference: str, manifest_digest: str,
                           optional: Optional[Dict[str, Any]] = None) -> bytes:
    """Simple Signing 载荷；critical.identity.docker-reference 被 cosign 忽略。"""
    obj = {
        "critical": {
            "identity": {"docker-reference": docker_reference},
            "image": {"Docker-manifest-digest": manifest_digest},
            "type": SIMPLE_SIGNING_TYPE,
        },
        "optional": optional or {},
    }
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------
# 6. Sigstore bundle
# --------------------------------------------------------------------------

BUNDLE_MEDIA_TYPES = {
    "0.1": "application/vnd.dev.sigstore.bundle+json;version=0.1",
    "0.2": "application/vnd.dev.sigstore.bundle+json;version=0.2",
    "0.3": "application/vnd.dev.sigstore.bundle+json;version=0.3",
}
BUNDLE_MEDIA_TYPE_CURRENT = "application/vnd.dev.sigstore.bundle.v0.3+json"


def accept_bundle_media_type(mt: str) -> bool:
    """现行必须产出 v0.3+json；客户端还需接受 0.1/0.2/0.3 的旧写法。

    口径说明：规范只说「客户端必须能接受此前定义的格式」，未说明未知版本怎么办，
    本 demo 取「未知版本拒绝」并在 NOTES 标注。
    """
    if mt == BUNDLE_MEDIA_TYPE_CURRENT:
        return True
    return mt in set(BUNDLE_MEDIA_TYPES.values())


def validate_bundle(bundle: Dict[str, Any]) -> List[str]:
    """bundle.proto 的若干 MUST：返回错误列表，空列表表示通过。"""
    errs: List[str] = []
    if not accept_bundle_media_type(bundle.get("mediaType", "")):
        errs.append("mediaType 不是可接受的 bundle 类型")
    if "verificationMaterial" not in bundle:
        errs.append("verificationMaterial 是 REQUIRED")
    dsse = bundle.get("dsseEnvelope")
    if dsse is not None:
        # bundle 里的 DSSE envelope 必须恰好一个签名（DSSE 本身允许多签，
        # 但 bundle 出于简化验证逻辑的原因强制单签）
        n = len(dsse.get("signatures", []))
        if n != 1:
            errs.append("bundle 内的 DSSE envelope 必须恰好一个签名, 实际 %d" % n)
        # verification material 给出 public key（key hint）时，两处 keyid 必须一致
        vm = bundle.get("verificationMaterial", {})
        hint = vm.get("publicKey", {}).get("hint")
        if hint is not None and keyid_of(dsse["signatures"][0]) != hint:
            errs.append("key hint 在 verificationMaterial 与 DSSE envelope 中不一致")
    return errs


def trust_integrated_time(entry: Dict[str, Any]) -> bool:
    """rekor.proto：inclusion_promise 缺失时 integrated_time MUST NOT be trusted。"""
    return "inclusionPromise" in entry


def canonicalized_body_must_match(entry: Dict[str, Any],
                                  bundle_sig: str) -> Optional[bool]:
    """canonicalized_body 若设置，其中的签名必须与 Bundle.content 的签名一致。

    未设置时返回 None（客户端自行构造等价载荷）。
    """
    if "canonicalizedBody" not in entry:
        return None
    return entry["canonicalizedBody"].get("sig") == bundle_sig


# --------------------------------------------------------------------------
# 7. PublicKeyDetails 枚举（sigstore_common.proto）
# --------------------------------------------------------------------------

PUBLIC_KEY_DETAILS = {
    5: "PKIX_ECDSA_P256_SHA_256",
    6: "PKIX_ECDSA_P256_HMAC_SHA_256",   # deprecated（RFC 6979 那一种）
    12: "PKIX_ECDSA_P384_SHA_384",
    13: "PKIX_ECDSA_P521_SHA_512",
    7: "PKIX_ED25519",
    8: "PKIX_ED25519_PH",
}
DEPRECATED_PUBLIC_KEY_DETAILS = {6}
