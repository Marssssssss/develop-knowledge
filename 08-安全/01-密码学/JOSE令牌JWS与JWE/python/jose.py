"""JOSE：JWS（RFC 7515）+ JWE（RFC 7516）的紧凑与 JSON 两种序列化。

紧凑序列化（Compact）只有一种形态：
    JWS: BASE64URL(UTF8(Protected)) ‖ '.' ‖ BASE64URL(Payload) ‖ '.' ‖ BASE64URL(Signature)
    JWE: 五个部分：Protected ‖ Encrypted Key ‖ IV ‖ Ciphertext ‖ Tag
JSON 序列化（General）才能表达「多个签名」或「多个收件人」，
且**头部被拆成三层**：protected（受完整性保护）/ unprotected（共享）/ header（每签名或每收件人）。
"""

import base64
import hashlib
import hmac
import json

from aes import (AES128, aes_key_wrap, aes_key_unwrap,
                 a128cbc_hs256_encrypt, a128cbc_hs256_decrypt)


# ------------------------------------------------------------------ base64url
def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def unb64u(s):
    if isinstance(s, str):
        s = s.encode("ascii")
    pad = b"=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def josecompact_json(obj):
    """JOSE 用的 JSON 必须是紧凑无空白的（空白会改变签名输入）。"""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ------------------------------------------------------------------------ JWS
def hs256(key, msg):
    return hmac.new(key, msg, hashlib.sha256).digest()


def jws_sign(protected, payload, key, alg=None, unprotected=None, crit_known=()):
    """紧凑序列化签名。`crit_known` 是本端理解的扩展头名集合。"""
    hdr = dict(protected)
    alg = hdr.get("alg", alg or "HS256")     # 以头里的 alg 为准
    hdr["alg"] = alg
    if "crit" in hdr:
        _check_crit(hdr, crit_known)
    p = b64u(josecompact_json(hdr))
    q = b64u(payload if isinstance(payload, bytes) else payload.encode("utf-8"))
    signing_input = (p + "." + q).encode("ascii")
    if alg == "HS256":
        sig = hs256(key, signing_input)
    elif alg == "none":
        sig = b""
    else:
        raise ValueError("unsupported alg")
    return p + "." + q + "." + b64u(sig), signing_input


def _check_crit(hdr, known):
    """RFC 7515 §4.1.11：crit 引用的头必须**同时出现**在本 JOSE 头里，且必须被理解。"""
    for name in hdr.get("crit", []):
        if name not in hdr:
            raise ValueError("crit 引用的头不在 JOSE 头里: %s" % name)
        if name not in known:
            raise ValueError("不认识的扩展头: %s" % name)
        if hdr.get(name) is None:
            raise ValueError("crit 引用的头必须有值: %s" % name)


class VerifyError(Exception):
    pass


def jws_verify(token, key, allowed_algs=("HS256",), key_alg=None, crit_known=()):
    """验证紧凑 JWS。

    三条防线（RFC 8725 §3.1）：
      1. `alg` 必须落在调用方允许的**集合**里 —— 不允许"跟着 token 走"；
      2. 若密钥自带 `alg`（JWK 的 alg 成员），必须与头部一致 —— 挡住密钥/算法混淆；
      3. 用 `compare_digest` 比对，避免计时侧信道。
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise VerifyError("紧凑 JWS 必须是 3 段")
    p, q, s = parts
    hdr = json.loads(unb64u(p))
    alg = hdr.get("alg")
    if alg not in allowed_algs:
        raise VerifyError("alg 不在允许集合内: %r" % alg)
    if key_alg is not None and key_alg != alg:
        raise VerifyError("密钥的 alg=%r 与头部的 alg=%r 不一致" % (key_alg, alg))
    if "crit" in hdr:
        _check_crit(hdr, crit_known)
    signing_input = (p + "." + q).encode("ascii")
    if alg == "none":
        if s != "":
            raise VerifyError("alg=none 时签名必须为空")
        return unb64u(q)
    if alg != "HS256":
        raise VerifyError("unsupported alg")
    expect = hs256(key, signing_input)
    if not hmac.compare_digest(expect, unb64u(s)):
        raise VerifyError("签名不匹配")
    return unb64u(q)


def jws_general(payload, signatures):
    """General JSON 序列化：payload 一份，signatures 是一个数组（可多方签名）。"""
    return {
        "payload": b64u(payload),
        "signatures": signatures,
    }


def jws_general_add(obj, protected, key, unprotected=None, alg="HS256"):
    """追加一个签名；protected 与 unprotected 的成员名**不得重叠**（RFC 7515 §7.2.1）。"""
    unprotected = dict(unprotected or {})
    overlap = set(protected) & set(unprotected)
    if overlap:
        raise ValueError("protected 与 unprotected 成员名重叠: %s" % overlap)
    p = b64u(josecompact_json(protected))
    signing_input = (p + "." + obj["payload"]).encode("ascii")
    entry = {"protected": p, "header": unprotected,
             "signature": b64u(hs256(key, signing_input)) if alg != "none" else ""}
    obj["signatures"].append(entry)
    return obj


def jws_general_verify(obj, key, allowed_algs=("HS256",)):
    for entry in obj["signatures"]:
        hdr = json.loads(unb64u(entry["protected"]))
        if hdr.get("alg") not in allowed_algs:
            continue
        si = (entry["protected"] + "." + obj["payload"]).encode("ascii")
        if hmac.compare_digest(hs256(key, si), unb64u(entry["signature"])):
            return dict(hdr, **entry.get("header", {}))
    raise VerifyError("没有任何一个签名可用给定密钥验证通过")


# ------------------------------------------------------------------------ JWE
def jwe_compact(protected_hdr, plaintext, kek=None, cek=None, iv=None, alg="A128KW"):
    """紧凑 JWE。

    AAD 是 **ASCII(BASE64URL(Protected))** 而不是整串 token —— 这一点最常被写错：
    把五个部分拼起来当 AAD，或者把整个 header 当 AAD，都会得到对不上的 tag。
    """
    alg = protected_hdr.get("alg", alg)      # 以头里的 alg 为准（RFC 8725 §3.1）
    hdr = josecompact_json(protected_hdr)
    p = b64u(hdr)
    aad = p.encode("ascii")
    iv = iv or bytes(range(16))
    if alg == "dir":
        if len(cek) != 32:
            raise ValueError("dir + A128CBC-HS256 需要 32 字节 CEK")
        encrypted_key = b""
    elif alg == "A128KW":
        encrypted_key = aes_key_wrap(kek, cek)
    else:
        raise ValueError("unsupported alg")
    ct, tag = a128cbc_hs256_encrypt(cek, iv, aad, plaintext)
    return ".".join([p, b64u(encrypted_key), b64u(iv), b64u(ct), b64u(tag)])


def jwe_decrypt(token, kek=None, cek=None, allowed=("A128KW", "dir")):
    parts = token.split(".")
    if len(parts) != 5:
        raise VerifyError("紧凑 JWE 必须是 5 段")
    p, ek, iv, ct, tag = parts
    hdr = json.loads(unb64u(p))
    if hdr.get("alg") not in allowed:
        raise VerifyError("alg 不在允许集合内: %r" % hdr.get("alg"))
    if hdr.get("enc") != "A128CBC-HS256":
        raise VerifyError("不支持的 enc: %r" % hdr.get("enc"))
    aad = p.encode("ascii")
    if hdr.get("alg") == "A128KW":
        cek = aes_key_unwrap(kek, unb64u(ek))
    return a128cbc_hs256_decrypt(cek, unb64u(iv), aad, unb64u(ct), unb64u(tag))
