"""RFC 7519（JWT）与 RFC 7515（JWS）的可执行模型 + 注册声明校验。

官方口径逐条对应（RFC 7515 §5.2 / §7.1、RFC 7519 §4.1）：

紧凑序列化（§7.1）：
    BASE64URL(UTF8(JWS Protected Header)) || '.' || BASE64URL(JWS Payload) || '.'
    || BASE64URL(JWS Signature)
签名输入（JWS Signing Input）是 **ASCII("header.payload")**，
即「紧凑表示中第二个句点之前、不含第二个句点」的那段 ASCII 字节。

附录 A.1 的官方向量（本模型逐字节对拍）：
    Protected Header  {"typ":"JWT",\\r\\n "alg":"HS256"}
      → eyJ0eXAiOiJKV1QiLA0KICJhbGciOiJIUzI1NiJ9
    Payload           {"iss":"joe",\\r\\n "exp":1300819380,\\r\\n
                       "http://example.com/is_root":true}
      → eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6Ly9leGFt
        cGxlLmNvbS9pc19yb290Ijp0cnVlfQ
    Signature         dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk
    密钥（JWK）       {"kty":"oct","k":"AyM1SysPpbyDfgZld3umj1qzKObwVMko
                       qQ-EstJQLr_T-1qS0gZH75aKtMN3Yj0iPS4hcgUuTwjAzZr1Z9CAow"}

附录 A.5 的无保护 JWS：{"alg":"none"} → eyJhbGciOiJub25lIn0，签名为**空字节串**，
紧凑表示以句点结尾。

注册声明（RFC 7519 §4.1）：
    exp  「current date/time MUST be before the expiration date/time」
    nbf  「current date/time MUST be after or equal to」该时刻
    aud  「If the principal processing the claim does not identify itself with a value
          in the 'aud' claim when this claim is present, then the JWT MUST be rejected.」
    时钟偏移：exp/nbf 都允许「some small leeway, usually no more than a few minutes」
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional, Tuple

# --- 附录 A.1 / A.5 的官方常量 --------------------------------------------
OFFICIAL_HEADER = '{"typ":"JWT",\r\n "alg":"HS256"}'
OFFICIAL_HEADER_B64 = "eyJ0eXAiOiJKV1QiLA0KICJhbGciOiJIUzI1NiJ9"
OFFICIAL_PAYLOAD = ('{"iss":"joe",\r\n "exp":1300819380,\r\n '
                    '"http://example.com/is_root":true}')
OFFICIAL_PAYLOAD_B64 = ("eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6"
                        "Ly9leGFtcGxlLmNvbS9pc19yb290Ijp0cnVlfQ")
OFFICIAL_SIGNATURE_B64 = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
OFFICIAL_JWK_K = ("AyM1SysPpbyDfgZld3umj1qzKObwVMkoqQ-EstJQLr_T-1qS0gZH75"
                  "aKtMN3Yj0iPS4hcgUuTwjAzZr1Z9CAow")
NONE_HEADER_B64 = "eyJhbGciOiJub25lIn0"


# --- base64url（无 padding）------------------------------------------------
def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    # base64url → 标准 base64：把 - 换成 +、_ 换成 /，并补回 padding
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


class JwtError(Exception):
    pass


# --- 紧凑序列化 ------------------------------------------------------------
def signing_input(header_b64: str, payload_b64: str) -> bytes:
    """JWS Signing Input = ASCII(header_b64 + '.' + payload_b64)。"""
    return (header_b64 + "." + payload_b64).encode("ascii")


def sign_hs256(header_b64: str, payload_b64: str, key: bytes) -> str:
    mac = hmac.new(key, signing_input(header_b64, payload_b64), hashlib.sha256)
    return b64url_encode(mac.digest())


def serialize(header_b64: str, payload_b64: str, signature_b64: str) -> str:
    return header_b64 + "." + payload_b64 + "." + signature_b64


def parse_compact(token: str) -> Tuple[str, str, str]:
    parts = token.split(".")
    if len(parts) != 3:
        raise JwtError(f"紧凑序列化必须有 3 段，实得 {len(parts)}")
    return parts[0], parts[1], parts[2]


def encode_jwt(header: Dict[str, Any], claims: Dict[str, Any], key: bytes,
               alg: str = "HS256") -> str:
    h = b64url_encode(json.dumps(header, separators=(",", ":"),
                                 ensure_ascii=False).encode("utf-8"))
    p = b64url_encode(json.dumps(claims, separators=(",", ":"),
                                 ensure_ascii=False).encode("utf-8"))
    if alg == "none":
        return serialize(h, p, "")
    if alg != "HS256":
        raise JwtError(f"本模型只实现 HS256 与 none，实得 {alg}")
    return serialize(h, p, sign_hs256(h, p, key))


def official_key() -> bytes:
    """附录 A.1 的对称密钥（JWK 的 k 字段，本身是 base64url）。"""
    return b64url_decode(OFFICIAL_JWK_K)


# --- 校验 ------------------------------------------------------------------
def verify_hs256(token: str, key: bytes, allowed_algs: Optional[List[str]] = None
                 ) -> Dict[str, Any]:
    """校验签名并返回 claims。allowed_algs 用于在调用侧钉死算法白名单。"""
    h_b64, p_b64, s_b64 = parse_compact(token)
    header = json.loads(b64url_decode(h_b64))
    alg = header.get("alg")
    allowed = allowed_algs if allowed_algs is not None else ["HS256"]
    if alg not in allowed:
        raise JwtError(f"alg={alg!r} 不在白名单 {allowed} 内")
    if alg == "none":
        if s_b64 != "":
            raise JwtError("alg=none 的签名必须是空字符串")
        return json.loads(b64url_decode(p_b64))
    expected = sign_hs256(h_b64, p_b64, key)
    if not hmac.compare_digest(expected, s_b64):
        raise JwtError("签名不匹配")
    return json.loads(b64url_decode(p_b64))


def validate_claims(claims: Dict[str, Any], now: float,
                    audience: Optional[str] = None,
                    issuer: Optional[str] = None,
                    leeway: float = 0.0) -> None:
    """按 RFC 7519 §4.1 校验时间类与受众类声明；不通过抛 JwtError。"""
    exp = claims.get("exp")
    if exp is not None:
        if not now < exp + leeway:
            raise JwtError(f"token 已过期：now={now} exp={exp}")
    nbf = claims.get("nbf")
    if nbf is not None:
        if not now >= nbf - leeway:
            raise JwtError(f"token 尚未生效：now={now} nbf={nbf}")
    aud = claims.get("aud")
    if aud is not None:
        # 「In the special case when the JWT has one audience, the aud value MAY be a
        #  single case-sensitive string」
        values = [aud] if isinstance(aud, str) else list(aud)
        if audience is None or audience not in values:
            raise JwtError(f"aud={values} 不含本方标识 {audience!r}，MUST be rejected")
    if issuer is not None and claims.get("iss") != issuer:
        raise JwtError(f"iss={claims.get('iss')!r} 与期望 {issuer!r} 不符")
