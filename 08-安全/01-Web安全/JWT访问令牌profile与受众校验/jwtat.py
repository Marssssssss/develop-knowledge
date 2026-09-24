"""JWT 访问令牌 profile（RFC 9068）与受众校验最小模型。

依据 RFC 9068《JSON Web Token (JWT) Profile for OAuth 2.0 Access Tokens》
（https://www.rfc-editor.org/rfc/rfc9068.txt ，35141 B 全文实读）：

§2.1 Header
  - JWT 访问令牌 MUST 被签名；MUST NOT 使用 "none"
  - 符合本规范的 AS 与 RS MUST 把 **RS256** 列入支持的签名算法
  - 注册媒体类型 "application/at+jwt"；MUST 把它写进 "typ" 头参数
  - 按 RFC 7515 §4.1.9 的建议省略 "application/" 前缀 → "typ" SHOULD 是 **at+jwt**
  - 目的：防止 OIDC ID Token 被资源服务器当成访问令牌接受

§2.2 Data Structure —— **七个 REQUIRED 声明**：
  iss / exp / aud / sub / client_id / iat / jti
  - sub：涉及资源拥有者的授权（如授权码）SHOULD 是资源拥有者的 subject id；
    不涉及资源拥有者的（如客户端凭证）SHOULD 是 AS 用来指代客户端应用的标识
  - §2.2.1 认证信息声明 auth_time / acr / amr 是 OPTIONAL，
    且在同一授权响应派生出的所有令牌里**取值固定不变**
  - §2.2.3 若授权请求带 scope，令牌 SHOULD 含 scope 声明；
    且其中每个 scope 串 MUST 对 aud 所指的资源有意义

§4 Validating JWT Access Tokens（资源服务器 MUST 按此顺序）
  1. "typ" MUST 是 at+jwt 或 application/at+jwt，其他一律拒绝
  2. 若已加密则解密；若注册时协商了加密而收到的未加密 → SHOULD 拒绝
  3. AS 的 issuer 标识 MUST 与 iss **精确相等**
  4. aud MUST 含一个「资源服务器认为属于自己的」资源指示值，否则拒绝
  5. 按 alg 验签（RFC 7515）；alg 为 none MUST 拒绝；MUST 只用 AS 提供的密钥
  6. 当前时间 MUST 早于 exp；实现 MAY 给几分钟的时钟偏移余量
  失败一律按 RFC 6750 §3.1 返回 **invalid_token**

§5 Security Considerations
  - 跨 JWT 混淆：AS MUST 用**互不相同**的 aud 值区分同一签发者发往不同资源的令牌
  - sub 混淆：AS 应阻止客户端注册任意 client_id，以免恶意客户端挑中高权限
    资源拥有者的 sub 去迷惑资源服务器的授权逻辑
"""

AT_JWT_TYPES = ("at+jwt", "application/at+jwt")
REQUIRED_CLAIMS = ("iss", "exp", "aud", "sub", "client_id", "iat", "jti")
OPTIONAL_AUTHN_CLAIMS = ("auth_time", "acr", "amr")

# §4 的检查顺序（本 demo 把 §2.2 的 REQUIRED 声明检查插在 typ 之后）
CHECK_ORDER = ("typ", "required_claims", "encryption", "iss", "aud", "alg", "exp")


class Token:
    """一个 JWT（可为访问令牌，也可为 ID Token 之类）。"""

    def __init__(self, header, claims, encrypted=False):
        self.header = dict(header)
        self.claims = dict(claims)
        self.encrypted = encrypted

    def aud_list(self):
        aud = self.claims.get("aud")
        if aud is None:
            return []
        return list(aud) if isinstance(aud, (list, tuple)) else [aud]

    def __repr__(self):
        return "Token(typ=%r, alg=%r)" % (self.header.get("typ"),
                                          self.header.get("alg"))


class ResourceServer:
    """资源服务器。resource_id 是它认为属于自己的资源指示值。"""

    def __init__(self, resource_id, issuer, leeway=0, require_encryption=False):
        self.resource_id = resource_id
        self.issuer = issuer
        self.leeway = leeway
        self.require_encryption = require_encryption

    def validate(self, tok, now, signature_ok=True):
        """§4。返回 (ok, error, failed_check)。error 恒为 invalid_token。"""
        for step in CHECK_ORDER:
            if step == "typ":
                if tok.header.get("typ") not in AT_JWT_TYPES:
                    return (False, "invalid_token", "typ")
            elif step == "required_claims":
                if any(c not in tok.claims for c in REQUIRED_CLAIMS):
                    return (False, "invalid_token", "required_claims")
            elif step == "encryption":
                if self.require_encryption and not tok.encrypted:
                    return (False, "invalid_token", "encryption")
            elif step == "iss":
                # MUST **精确**相等
                if tok.claims.get("iss") != self.issuer:
                    return (False, "invalid_token", "iss")
            elif step == "aud":
                if self.resource_id not in tok.aud_list():
                    return (False, "invalid_token", "aud")
            elif step == "alg":
                if tok.header.get("alg") == "none":
                    return (False, "invalid_token", "alg")
                if not signature_ok:
                    return (False, "invalid_token", "alg")
            elif step == "exp":
                if now >= tok.claims.get("exp", 0) + self.leeway:
                    return (False, "invalid_token", "exp")
        return (True, None, None)


def make_access_token(issuer, audience, sub, client_id, now, ttl=300,
                      typ="at+jwt", alg="RS256", extra=None):
    """按 §2.1/§2.2 造一个合规的 JWT 访问令牌。"""
    claims = {
        "iss": issuer,
        "exp": now + ttl,
        "aud": audience,
        "sub": sub,
        "client_id": client_id,
        "iat": now,
        "jti": "jti-%d-%s" % (now, sub),
    }
    if extra:
        claims.update(extra)
    return Token({"typ": typ, "alg": alg}, claims)
