# JWT 验证(JSON Web Token,RFC 7519)

## 简介

JSON Web Token(JWT)是一种**紧凑、URL-safe 的声明表示格式**,常用于 HTTP `Authorization: Bearer <token>` 头或 URL 参数。结构是三段 base64url(`header.payload.signature`),由 RFC 7519(May 2015,Standards Track)标准化。**核心安全点**:服务端必须**严格校验签名**(签名算法 + 密钥),任何"忽略签名 / 算法替换 / `alg: none` 接受"的实现都会导致身份冒用。OWASP JWT Cheat Sheet(RFC 8725)是必读规范。

## 关键概念清单

| 术语 | 一句话 |
| --- | --- |
| **JWT** | 三段 base64url:`header.payload.signature`,用 `.` 连接 |
| **JWS** | JSON Web Signature,signature 是 header+payload 的 HMAC/RSA/ECDSA 签名 |
| **JWE** | JSON Web Encryption,加密 token(本 demo 不涉及) |
| **JWK** | JSON Web Key,公钥/密钥的 JSON 表示 |
| **HS256 / RS256 / ES256 / EdDSA** | 签名算法(MAC / RSA-PKCS1 / ECDSA P-256 / Ed25519) |
| **`alg: none`** | 无签名,OWASP/Auth0 历史上多次爆漏洞 |
| **base64url** | URL-safe base64(`-_` 替代 `+/`,无 padding) |
| **Registered Claims** | `iss` `sub` `aud` `exp` `nbf` `iat` `jti` 七项标准声明 |
| **`kid`** | Key ID header,用于多密钥环境选 key |
| **`typ`** | Token 类型(JWT / access+JWT 等) |

## 历史背景

- 2010-2011 年 OAuth WG 提出 JWT 草案
- 2014-2015 年 RFC 7519(JWT)+ RFC 7515(JWS)+ RFC 7516(JWE)+ RFC 7517(JWK)四件套发布
- 2015 年 Auth0 `jsonwebtoken` 库爆出 `alg: none` 接受漏洞(CVE-2015-9235)
- 2018 年 RFC 8725 *JWT Best Current Practices* 发布,系统化安全建议
- 2020 年起 OAuth 2.0 + JWT 成为事实标准身份 token,OpenID Connect ID Token 也基于 JWT

## 原理详解

### JWT 紧凑序列化(Compact JWS)

```
header   .   payload   .   signature
   ↑              ↑              ↑
   base64url      base64url      base64url(HMAC/RSA/etc.)
```

**生成步骤**(HS256 为例):

```
1. header  = {"typ":"JWT", "alg":"HS256"}
2. payload = {"iss":"joe", "sub":"123", "exp":1700000000, "scope":"read"}
3. signing_input = base64url(header) + "." + base64url(payload)
4. signature = HMAC-SHA256(secret, signing_input)            ← 32 bytes
5. JWT = signing_input + "." + base64url(signature)
```

**base64url 注意**:RFC 7515 §2 — `base64url` 是 base64 的 URL/文件名安全变体:
- `+` → `-`
- `/` → `_`
- 末尾 `=` padding **删除**

### 注册声明(Registered Claims,RFC 7519 §4.1)

| 名 | 全称 | 必填? | 说明 |
| --- | --- | --- | --- |
| `iss` | Issuer | 否 | token 签发方 |
| `sub` | Subject | 否 | token 主体(用户 ID) |
| `aud` | Audience | 否 | token 接收方(可数组) |
| `exp` | Expiration | 否 | Unix 时间戳,过此时刻无效 |
| `nbf` | Not Before | 否 | Unix 时间戳,此时刻前无效 |
| `iat` | Issued At | 否 | Unix 时间戳,签发时间 |
| `jti` | JWT ID | 否 | 唯一标识(防重放) |

### 校验流程(RFC 7519 §7.2 + RFC 8725)

```
1. 拆三段(header.payload.signature),base64url 解码 header
2. 检查 alg:必须是算法白名单(拒绝 alg=none、alg=HS256 与 RSA 公钥混用)
3. 根据 alg 取密钥(HS256 用共享 secret,RS256 用公钥)
4. 重新计算 signature' = base64url(HMAC/RSA_verify(signing_input))
5. constant-time 比对 signature == signature'
6. 解析 payload,校验 exp(> now), nbf(< now), iss/aud
7. (可选)检查 jti 黑名单或 Redis 撤销表
```

### ⚠️ RFC 8725 安全警告(BCP)

| 攻击 | 描述 | 防御 |
| --- | --- | --- |
| **Algorithm Confusion** | 服务端用 RSA 公钥验签,但接受 `alg: HS256`(用公钥当 HMAC secret) | **强制白名单算法**,不要按 header 选算法 |
| **`alg: none`** | 攻击者改 header `alg: none`,部分库接受 | **永远拒绝 `alg: none`** |
| **Weak HMAC secret** | `secret="password"` | secret ≥ 256 bits(HS256 输出长度) |
| **Stripped signature** | `signature=""` | 强制 signature 非空 |
| **Replay** | 偷到 token 可重用 | 短 exp + jti 黑名单 + 设备指纹 |
| **Cross-JWT confusion** | 不同服务密钥相同时 token 可混用 | 强 `kid` + 严格 `iss`/`aud` |

### 头部参数(JOSE Header,RFC 7515 §4)

| 参数 | 说明 |
| --- | --- |
| `alg` | 算法(必须) |
| `typ` | 类型,如 `JWT` |
| `cty` | 内容类型,如嵌套 JWT 写 `JWT` |
| `kid` | Key ID,密钥轮换时定位 |
| `jku` | JWK Set URL(慎重,验证 HTTPS + 白名单) |
| `x5u` / `x5c` / `x5t` | X.509 证书链(慎用) |

## 演示

### Demo 1: 编码/解码 base64url

```python
b64 = base64url_encode(b'\xfb\xff')           # 避免 +,/,=
b64_decode = base64url_decode(b64)            # 还原字节
```

### Demo 2: JWT 签发(HS256)

```python
token = jwt_encode(header={'alg':'HS256','typ':'JWT'},
                   payload={'sub':'alice','exp':...},
                   secret=b'32-bytes-long-secret-XXXXXXXX')
```

### Demo 3: JWT 验证(签名 + 过期)

```python
claims = jwt_decode(token, secret=b'...', algorithms=['HS256'])
# 篡改 token → InvalidSignatureError
# 过期 token → ExpiredSignatureError
```

### Demo 4: Algorithm Confusion 攻击演示

```python
# 攻击:伪造 header 替换 alg
# 服务端按 alg 选算法 → 若用 RSA 公钥 + alg:HS256 → 用公钥当 HMAC secret 即可伪造!
# 防御:算法白名单 + 强制 alg 匹配密钥类型
```

### Demo 5: RFC 7519 附录 A 示例向量验证

```python
# RFC 7519 §A.1 HS256 示例
header_b64 = "eyJ0eXAiOiJKV1QiLA0KICJhbGciOiJIUzI1NiJ9"
payload_b64 = "eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6Ly9leGFtcGxlLmNvbS9pc19yb290Ijp0cnVlfQ"
# 用 RFC 给的 key 验签应通过
```

### Demo 6: 篡改检测(改 payload 一字节)

```python
token = "header.payload.sig"
tampered = token.replace("payload", "payloaX")   # 改 1 字符
# jwt_decode(tampered, ...) → InvalidSignatureError
```

## 环境准备

- Python 3.8+(纯标准库 `hmac` + `hashlib` + `base64`,无 PyJWT 依赖)
- Go 1.18+ (运行 Go demo,stdlib `crypto/hmac` + `crypto/sha256` + `encoding/base64`)

## 运行方式

```bash
cd JWT验证/python
python3 jwt_demo.py
# 6 demo:base64url 编解码 / HS256 签发 / 签名+过期校验 / alg confusion /
# RFC 7519 附录 A 向量 / 篡改检测
```

```bash
cd JWT验证/go
go run jwt_demo.go
```

## 关键代码片段

```python
# 1. base64url 编码(RFC 7515 §2)
def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

# 2. HMAC-SHA256 签发
def jwt_sign_hs256(header: dict, payload: dict, secret: bytes) -> str:
    h = b64url_encode(json.dumps(header,  separators=(',',':')).encode())
    p = b64url_encode(json.dumps(payload, separators=(',',':')).encode())
    signing_input = f'{h}.{p}'.encode()
    sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    return f'{h}.{p}.{b64url_encode(sig)}'

# 3. 校验(强制算法白名单)
def jwt_verify_hs256(token: str, secret: bytes) -> dict:
    h_b64, p_b64, sig_b64 = token.split('.')
    # ★ RFC 8725:先校验 alg,绝不要按 header 选算法
    header = json.loads(b64url_decode(h_b64))
    assert header['alg'] == 'HS256', 'algorithm not allowed'
    signing_input = f'{h_b64}.{p_b64}'.encode()
    expected_sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    actual_sig = b64url_decode(sig_b64)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError('signature mismatch')
    payload = json.loads(b64url_decode(p_b64))
    if 'exp' in payload and payload['exp'] < time.time():
        raise ValueError('token expired')
    return payload
```

## 性能与边界

- **HMAC-SHA256 签名/验签**:单次 ~1 μs(Python hmac)
- **JWT 长度**:header ~36 chars + payload 变长 + signature 43 chars = 通常 150-300 chars
- **Token 大小限制**:HTTP Header 单行 8KB,实际建议 ≤ 1KB
- **验签 O(n)**:n = token 长度,与 header/payload 大小无关

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| `alg: none` 被接受 | 库默认允许 | **显式 algorithms 白名单**(`['HS256']`) |
| 算法替换(HS256 → RS256) | 服务端按 header 选算法 + 用公钥当 HMAC secret | 强制 alg 与密钥类型匹配 |
| 弱 HMAC secret | `secret="password123"` | secret ≥ 32 bytes 随机;或用 RS256(私钥签名) |
| 过期 token 仍可用 | 没校验 `exp` | 校验时 `if exp < now: raise` |
| 时钟偏差导致误拒 | 服务端与签发方时钟差 1 分钟 | exp 加 ±60s 容忍(但不要过大) |
| 重放攻击 | 偷到 token 可重用 | 短 exp(15min) + jti 黑名单 + refresh token |
| payload 含敏感数据 | JWT payload 仅 base64url 编码,不加密**绝不**写密码 | 用 JWE 加密 或仅放用户 ID |
| `sub` 用 email / UUID | 泄露 PII | 用内部 ID,或 hash(email) |
| `kid` 信任 header 值 | `jku: https://attacker.com/keys.json` | 强制 kid 白名单 + HTTPS |

## 参考资料(实际阅读)

- [RFC 7519 — JSON Web Token (JWT)](https://datatracker.ietf.org/doc/html/rfc7519) — JWT 标准,7 个注册声明
- [RFC 7515 — JSON Web Signature (JWS)](https://datatracker.ietf.org/doc/html/rfc7515) — 签名结构与 base64url
- [RFC 8725 — JSON Web Token Best Current Practices](https://datatracker.ietf.org/doc/html/rfc8725) — BCP 安全建议
- [RFC 7517 — JSON Web Key (JWK)](https://datatracker.ietf.org/doc/html/rfc7517) — 公钥 JSON 表示
- [OWASP JSON Web Token Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/JSON_Web_Token_for_Java_Cheat_Sheet.html) — 实战安全建议
- [Auth0 JWT 库历史漏洞](https://auth0.com/blog/critical-vulnerabilities-in-json-web-token-libraries/) — CVE-2015-9235 `alg:none` 复盘
- [PortSwigger Web Security Academy - JWT attacks](https://portswigger.net/web-security/jwt) — 互动实验