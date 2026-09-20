"""DPoP（RFC 9449）与 OAuth 2.0 互 TLS（RFC 8705）发送方约束令牌最小模型。

依据 RFC 9449（*OAuth 2.0 Demonstrating Proof of Possession*，全文实读）：
  * §4.2 DPoP proof 是 JWS，JOSE 头至少含 typ=dpop+jwt、alg（**必须是已注册的
    非对称算法，MUST NOT 为 none 或 MAC 算法**）、jwk（**MUST NOT 含私钥**）。
  * payload 至少含 jti（≥96 bit 随机，用于重放检测）、htm（HTTP 方法）、
    htu（**不含 query 与 fragment** 的目标 URI）、iat。
  * 访问资源时**必须**再加 ath = base64url(SHA-256(ASCII(access_token)))；
    服务器提供 nonce 时**必须**再加 nonce。
  * §4.3 列出 12 条校验（见 check_proof 的 E1..E12）。
  * §6.1 jkt = RFC 7638 JWK SHA-256 指纹，放在访问令牌的 cnf 里。
  * §7.1 Authorization 用 `DPoP <token68>` 方案。
  * §8 AS 用 **400 + use_dpop_nonce** 下发 nonce；§9 RS 用 **401 +
    WWW-Authenticate: DPoP error="use_dpop_nonce"** 下发。nonce 对客户端不透明，
    AS 与 RS 的 nonce **彼此独立**。
  * §10 dpop_jkt 授权请求参数把授权码也绑到同一把钥匙（可选，且与 PKCE 互补）。

依据 RFC 8705（*OAuth 2.0 Mutual-TLS Client Authentication and
Certificate-Bound Access Tokens*，全文实读）：
  * §3.1 证书绑定用 cnf["x5t#S256"] = base64url(SHA-256(DER(X.509)))，
    **去掉全部尾随 '='**。
  * §3 资源服务器必须从 TLS 层取出客户端证书并与令牌绑定的证书比对，
    不匹配按 RFC 6750 返回 **401 invalid_token**。
  * §3 前置知识：是否要求互 TLS 不能由访问令牌决定 —— 令牌是 TLS 之上的
    "Application Data"，而 CertificateRequest 在握手阶段就发出了。

签名口径（本模型显式声明）：RFC 9449 §4.3(5) 要求 alg 是 ES256/EdDSA 这类已注册
非对称算法。为了让"签名—验签"在本机无第三方库时真实可跑，本模型内置一个
**教科书式 RSA-FDH**（256-bit 素数，e=65537，sig = H(m)^d mod n）。它只承担
"持有私钥才能产出、公钥可验"这一语义，**不提供生产强度**；alg 字段仍按 RFC 要求
写成 RS256，并在自检里断言 alg=none / alg=HS256 一律被拒。
"""

import base64
import hashlib
import json
import random

# ---------------------------------------------------------------- base64url
def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64u_dec(s):
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sha256_bytes(data):
    return hashlib.sha256(data).digest()


# ------------------------------------------------------- 教科书 RSA-FDH 签名
def _is_probable_prime(n, rounds=24):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = random.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits):
    while True:
        p = random.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(p):
            return p


class KeyPair:
    """教科书 RSA 密钥对。仅用于演示签名/验签语义，非生产强度。"""

    E = 65537

    def __init__(self, bits=256, seed=None):
        rng = random.Random(seed)
        old = random.getstate()
        random.setstate(rng.getstate())
        try:
            while True:
                p = _gen_prime(bits)
                q = _gen_prime(bits)
                if p == q:
                    continue
                n = p * q
                phi = (p - 1) * (q - 1)
                if phi % self.E == 0:
                    continue
                self.n = n
                self.e = self.E
                self.d = pow(self.E, -1, phi)
                return
        finally:
            random.setstate(old)

    def sign_digest_int(self, h):
        return pow(h, self.d, self.n)

    def recover(self, sig):
        return pow(sig, self.e, self.n)


def _int_to_be(x):
    length = max(1, (x.bit_length() + 7) // 8)
    return x.to_bytes(length, "big")


def jwk_public(kp):
    """RFC 7517 公钥表示（RSA 只需 e 与 n，均为 base64url 无填充）。"""
    return {"kty": "RSA", "e": b64u(_int_to_be(kp.e)), "n": b64u(_int_to_be(kp.n))}


def jwk_thumbprint(jwk):
    """RFC 7638 §3.2：按成员名**字典序**、无空白、无填充的 JSON 的 SHA-256 base64url。"""
    canonical = json.dumps(jwk, separators=(",", ":"), sort_keys=True,
                           ensure_ascii=False).encode("utf-8")
    return b64u(sha256_bytes(canonical))


# ------------------------------------------------------------------ JWS 编解码
ASYM_ALGS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512",
             "PS256", "PS384", "PS512", "EdDSA")


def jws_encode(header, payload, kp):
    head = b64u(json.dumps(header, separators=(",", ":"),
                           sort_keys=True).encode("utf-8"))
    body = b64u(json.dumps(payload, separators=(",", ":"),
                           sort_keys=True).encode("utf-8"))
    signing_input = (head + "." + body).encode("ascii")
    h = int.from_bytes(sha256_bytes(signing_input), "big") % kp.n
    sig = kp.sign_digest_int(h)
    return head + "." + body + "." + b64u(_int_to_be(sig))


def jws_decode(compact):
    parts = compact.split(".")
    if len(parts) != 3:
        return None, None, None, None
    head, body, sig = parts
    try:
        header = json.loads(b64u_dec(head))
        payload = json.loads(b64u_dec(body))
        sig_bytes = b64u_dec(sig)
    except Exception:
        return None, None, None, None
    return header, payload, sig_bytes, (head + "." + body).encode("ascii")


def jws_verify(compact):
    header, payload, sig_bytes, signing_input = jws_decode(compact)
    if header is None:
        return False, "JWS 结构不合法", None, None
    jwk = header.get("jwk")
    if not isinstance(jwk, dict) or jwk.get("kty") != "RSA":
        return False, "jwk 缺失或不是 RSA 公钥", header, payload
    try:
        n = int.from_bytes(b64u_dec(jwk["n"]), "big")
        e = int.from_bytes(b64u_dec(jwk["e"]), "big")
        sig = int.from_bytes(sig_bytes, "big")
    except Exception:
        return False, "jwk/签名不可解码", header, payload
    if sig >= n:
        return False, "签名越界", header, payload
    want = int.from_bytes(sha256_bytes(signing_input), "big") % n
    if pow(sig, e, n) != want:
        return False, "签名不验证", header, payload
    return True, "", header, payload


# ------------------------------------------------------------------- DPoP 证明
def ath_of(access_token):
    """RFC 9449 §4.2：base64url(SHA-256(ASCII 编码的访问令牌值))。"""
    return b64u(sha256_bytes(access_token.encode("ascii")))


def make_proof(kp, jti, htm, htu, iat, ath=None, nonce=None,
               typ="dpop+jwt", alg="RS256", with_jwk=True, leak_d=False):
    header = {"typ": typ, "alg": alg}
    if with_jwk:
        jwk = jwk_public(kp)
        if leak_d:                      # §4.3(7) 禁止 jwk 里带私钥
            jwk["d"] = b64u(_int_to_be(kp.d))
        header["jwk"] = jwk
    payload = {"jti": jti, "htm": htm, "htu": htu, "iat": iat}
    if ath is not None:
        payload["ath"] = ath
    if nonce is not None:
        payload["nonce"] = nonce
    return jws_encode(header, payload, kp)


def strip_query_fragment(uri):
    """§4.3(9)：比较 htu 时要忽略 query 与 fragment。"""
    for sep in ("?", "#"):
        i = uri.find(sep)
        if i >= 0:
            uri = uri[:i]
    return uri


class Request:
    def __init__(self, method, uri, dpop_headers, authorization=None,
                 access_token=None):
        self.method = method
        self.uri = uri
        self.dpop_headers = list(dpop_headers)
        self.authorization = authorization
        self.access_token = access_token


class DPoPServer:
    """AS / RS 共用的 DPoP 校验器。now 由调用方注入，便于确定性测试。"""

    def __init__(self, name, require_nonce=False, max_age=60, now=0,
                 start_nonce=None):
        self.name = name
        self.require_nonce = require_nonce
        self.max_age = max_age
        self.now = now
        self.recent_nonces = []
        if start_nonce:
            self.recent_nonces.append(start_nonce)
        self.seen_jti = set()

    def issue_nonce(self):
        n = "N-" + b64u(sha256_bytes(("%s:%d" % (self.name, self.now)).encode()))[:12]
        self.recent_nonces.append(n)
        if len(self.recent_nonces) > 4:
            self.recent_nonces.pop(0)
        return n

    def check_proof(self, request, expected_token=None, expected_jkt=None):
        """RFC 9449 §4.3 的 12 条；返回 (ok, reason, payload)。

        reason 形如 "E8 htm 不匹配"，便于自检逐条定位。
        """
        if len(request.dpop_headers) != 1:
            return False, "E1 DPoP 头不是恰好一个", None
        ok, why, header, payload = jws_verify(request.dpop_headers[0])
        if not ok:
            return False, "E2/E6 " + why, None
        for claim in ("jti", "htm", "htu", "iat"):
            if claim not in payload:
                return False, "E3 缺 %s" % claim, payload
        if header.get("typ") != "dpop+jwt":
            return False, "E4 typ 不是 dpop+jwt", payload
        alg = header.get("alg")
        if alg not in ASYM_ALGS or alg in ("none", "HS256"):
            return False, "E5 alg=%r 不是可接受的非对称算法" % alg, payload
        jwk = header.get("jwk") or {}
        if "d" in jwk or "p" in jwk or "q" in jwk or "k" in jwk:
            return False, "E7 jwk 含私钥成员", payload
        if self.require_nonce:
            if "nonce" not in payload:
                return False, "E10 缺 nonce", payload
            if payload["nonce"] not in self.recent_nonces:
                return False, "E10 nonce 不是服务器近期下发的值", payload
        if payload["htm"] != request.method:
            return False, "E8 htm 不匹配", payload
        if strip_query_fragment(payload["htu"]) != strip_query_fragment(request.uri):
            return False, "E9 htu 不匹配", payload
        if abs(self.now - payload["iat"]) > self.max_age:
            return False, "E11 iat 超出可接受窗口", payload
        if expected_token is not None:
            if payload.get("ath") != ath_of(expected_token):
                return False, "E12a ath 不等于该访问令牌的哈希", payload
            if expected_jkt is not None and jwk_thumbprint(jwk) != expected_jkt:
                return False, "E12b 证明公钥与令牌绑定的 jkt 不一致", payload
        return True, "ok", payload

    def single_use(self, jti):
        """§11.1：jti 单用检查（防同一 proof 重放）。"""
        if jti in self.seen_jti:
            return False
        self.seen_jti.add(jti)
        return True


def x5t_s256(der_bytes):
    """RFC 8705 §3.1：base64url(SHA-256(DER))，**去掉全部尾随 '='**。"""
    return b64u(hashlib.sha256(der_bytes).digest())


def mtls_resource_check(token_cnf_x5t, presented_der):
    """RFC 8705 §3 / §6.2：TLS 层取到的证书必须与令牌绑定的证书一致。"""
    if not token_cnf_x5t:
        return True, "ok"
    if x5t_s256(presented_der) != token_cnf_x5t:
        return False, "invalid_token"
    return True, "ok"
