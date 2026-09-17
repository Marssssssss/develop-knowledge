"""OAuth 2.0 授权码 + PKCE 最小实现(RFC 6749 §4.1 / RFC 7636 §4 / RFC 9700 §2.1)。

复刻的是"协议语义"而非 HTTP 服务端:每个函数对应规范里的一条 MUST。
权威来源见 README「参考资料」。

- code_verifier/code_challenge 变换:RFC 7636 §4.2 / §4.6(附录 B 向量是自检基准)
- 授权码单次使用 + 10 分钟生命周期:RFC 6749 §4.1.2 / RFC 9700 §4.2.4
- redirect_uri 精确字符串匹配 + 失败不得重定向:RFC 6749 §3.1.2.3 / §3.1.2.4
- PKCE 降级(有 verifier 无 challenge):RFC 9700 §2.1.1、§4.8.2
- mix-up 的 issuer 识别(RFC 9207 iss):RFC 9700 §4.4.2.1
"""
from __future__ import annotations

import base64
import hashlib
import hmac

CODE_TTL = 600          # RFC 6749 §4.1.2:最长 10 分钟(推荐值)
VERIFIER_MIN, VERIFIER_MAX = 43, 128   # RFC 7636 §4.1 ABNF: 43*128unreserved
UNRESERVED = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


def b64url(raw: bytes) -> str:
    """RFC 7636 §Appendix A:base64 去掉尾部 '='、'+'→'-'、'/'→'_'。"""
    return base64.b64encode(raw).decode("ascii").rstrip("=").replace("+", "-").replace("/", "_")


def s256(verifier: str) -> str:
    """RFC 7636 §4.2: BASE64URL-ENCODE(SHA256(ASCII(code_verifier)))。"""
    return b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def verifier_syntax_ok(verifier: str) -> bool:
    return (
        VERIFIER_MIN <= len(verifier) <= VERIFIER_MAX
        and all(c in UNRESERVED for c in verifier)
    )


class OAuthError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class AuthServer:
    """授权服务器:注册表 + 授权端点 + 令牌端点。time 由调用方注入,便于控制过期。"""

    def __init__(self, issuer: str, now=0):
        self.issuer = issuer
        self.now = now
        self.clients: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.tokens: dict[str, list[str]] = {}
        self.revoked: set[str] = set()
        self._seq = 0

    # ---------- 注册 ----------
    def register(self, client_id: str, redirect_uris: list[str], public: bool = True):
        self.clients[client_id] = {
            "redirect_uris": list(redirect_uris),   # §3.1.2.2:须预注册完整 URI
            "public": public,
            "secret": None if public else f"s3cret-{client_id}",
        }

    # ---------- 授权端点 ----------
    def authorize(self, req: dict) -> str:
        """返回重定向 URL;按 §3.1.2.4,URI 校验失败时抛错而**不**重定向。"""
        client = self.clients.get(req.get("client_id", ""))
        if client is None:
            raise OAuthError("invalid_request", "unknown client_id")
        uri = req.get("redirect_uri", "")
        # RFC 9700 §2.1 / §4.1.3:与预注册值做**精确字符串匹配**(仅原生 localhost 端口可变)
        if uri not in client["redirect_uris"]:
            raise OAuthError("invalid_request", "redirect_uri not registered exactly")
        if not uri.startswith("https://") and not uri.startswith("http://127.0.0.1"):
            # RFC 9700 §2.6:授权服务器 MUST NOT 允许 http scheme(回环地址除外)
            raise OAuthError("invalid_request", "redirect_uri must not use http scheme")

        # RFC 7636 §4.4.1:服务端要求 PKCE 而客户端未带 challenge → invalid_request
        challenge = req.get("code_challenge")
        if client["public"] and not challenge:
            raise OAuthError("invalid_request", "code challenge required")
        method = req.get("code_challenge_method", "plain")   # §4.3 缺省 plain
        if challenge and method not in ("plain", "S256"):
            raise OAuthError("invalid_request", "transform algorithm not supported")
        if challenge and method == "plain":
            pass  # §7.2 SHOULD use S256;此处仅记录,demo 不阻断

        self._seq += 1
        code = f"code-{self.issuer}-{self._seq}"
        self.codes[code] = {
            "client_id": req["client_id"],
            "redirect_uri": uri,
            "challenge": challenge,
            "method": method,
            "issued_at": self.now,
            "used": False,
            "state": req.get("state"),
        }
        sep = "&" if "?" in uri else "?"
        # RFC 9700 §4.1.3:附加 '#' 片段可阻止浏览器把原片段重新粘回重定向 URL
        frag = ""
        if req.get("attach_fragment"):
            frag = "#_"
        # RFC 9207:授权响应携带 ran 绑定的 issuer,供客户端做 mix-up 校验
        return (f"{uri}{sep}code={code}&state={req.get('state','')}"
                f"&iss={self.issuer}{frag}")

    def deny(self, req: dict, error: str) -> str:
        uri = req["redirect_uri"]
        sep = "&" if "?" in uri else "?"
        return f"{uri}{sep}error={error}&state={req.get('state','')}"

    # ---------- 令牌端点 ----------
    def token(self, req: dict) -> dict:
        client = self.clients.get(req.get("client_id", ""))
        if client is None:
            raise OAuthError("invalid_client")
        if not client["public"]:
            if req.get("client_secret") != client["secret"]:
                raise OAuthError("invalid_client", "bad secret")
        code = self.codes.get(req.get("code", ""))
        if code is None:
            raise OAuthError("invalid_grant", "unknown code")
        if code["client_id"] != req["client_id"]:
            raise OAuthError("invalid_grant", "code issued to another client")

        verifier = req.get("code_verifier")
        # RFC 9700 §2.1.1 / §4.8.2:只有授权请求带了 challenge,才接受 verifier
        if not code["challenge"] and verifier is not None:
            raise OAuthError("invalid_grant", "code_verifier without code_challenge (downgrade)")
        if code["challenge"] and verifier is None:
            raise OAuthError("invalid_request", "code_verifier required")
        if code["challenge"]:
            if not verifier_syntax_ok(verifier):
                raise OAuthError("invalid_grant", "code_verifier syntax")
            expected = verifier if code["method"] == "plain" else s256(verifier)
            # RFC 7636 §4.6:不相等时 MUST 返回 invalid_grant(用 compare_digest 防时序侧信道)
            if not hmac.compare_digest(expected, code["challenge"]):
                raise OAuthError("invalid_grant", "code_verifier mismatch")

        # RFC 6749 §4.1.2:码单次使用;§4.1.3:redirect_uri 必须与授权请求完全一致
        if code["used"]:
            # RFC 9700 §4.2.4:重放时 SHOULD 撤销先前基于该码签发的全部令牌
            for tok in self.tokens.get(req["code"], []):
                self.revoked.add(tok)
            raise OAuthError("invalid_grant", "code already used; tokens revoked")
        if self.now - code["issued_at"] > CODE_TTL:
            raise OAuthError("invalid_grant", "code expired")
        if req.get("redirect_uri") != code["redirect_uri"]:
            raise OAuthError("invalid_grant", "redirect_uri mismatch")
        if code["challenge"] and req.get("code_verifier"):
            pass

        code["used"] = True
        self._seq += 1
        access = f"at-{self._seq}"
        refresh = f"rt-{self._seq}"
        self.tokens[req["code"]] = [access, refresh]
        return {"access_token": access, "token_type": "Bearer",
                "expires_in": 3600, "refresh_token": refresh}


class Client:
    """客户端:为**每个**授权请求新建 state + verifier,并记住本次请求的 issuer。"""

    def __init__(self, client_id: str, redirect_uri: str, issuer: str):
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.issuer = issuer          # RFC 9700 §4.4.2:必须存"请求发给了谁"
        self.sessions: dict[str, dict] = {}
        self._n = 0

    def start(self, rng_bytes: bytes) -> dict:
        self._n += 1
        state = f"st-{self._n}"
        verifier = b64url(rng_bytes)[:43]
        sess = {"state": state, "verifier": verifier, "used": False,
                "issuer": self.issuer, "redirect_uri": self.redirect_uri}
        self.sessions[state] = sess
        return {
            "authorize_url": f"{self.issuer}/authorize",
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": s256(verifier),      # §4.1 推荐 32 字节随机 → 43 字符
            "code_challenge_method": "S256",
        }

    def callback(self, query: dict, token_endpoint_issuer: str) -> dict:
        state = query.get("state")
        sess = self.sessions.get(state or "")
        if sess is None or sess["used"]:
            raise OAuthError("state_mismatch", "CSRF / replay")
        sess["used"] = True        # RFC 9700 §4.2.4:state 首次使用后即失效
        # RFC 9700 §4.4.2.1:收到的 iss 必须与请求时存的 issuer 相同,否则中止
        got_iss = query.get("iss")
        if got_iss is not None and got_iss != sess["issuer"]:
            raise OAuthError("mix_up", f"iss={got_iss} != {sess['issuer']}")
        if token_endpoint_issuer != sess["issuer"]:
            raise OAuthError("mix_up", "token endpoint belongs to another AS")
        return {
            "grant_type": "authorization_code",
            "code": query["code"],
            "redirect_uri": sess["redirect_uri"],
            "client_id": self.client_id,
            "code_verifier": sess["verifier"],
        }
