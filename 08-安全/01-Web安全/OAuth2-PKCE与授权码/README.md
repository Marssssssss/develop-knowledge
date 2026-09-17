# OAuth 2.0 授权码流程与 PKCE

> 一个"协议语义级"最小实现:把 RFC 6749 §4.1、RFC 7636、RFC 9700 里那些 **MUST** 逐条变成可断言的代码。
> 不搭 HTTP 服务器 —— 因为本 demo 要验证的是**判定逻辑**(谁能拿到令牌、什么时候必须拒绝),不是网络管道。

## 简介

授权码流程是 OAuth 2.0 的默认姿势,但它有三个"看起来是细节、其实是命门"的地方:

| 命门 | 攻击后果 | 规范给的强制项 |
| --- | --- | --- |
| `redirect_uri` 用前缀/正则匹配 | 授权码被投递到攻击者控制的 URL | 精确字符串匹配(RFC 9700 §2.1),校验失败**不得**重定向(RFC 6749 §3.1.2.4) |
| 公共客户端不带 PKCE | 授权码被截获后在令牌端点直接兑换 | 公共客户端 **MUST** 用 PKCE,授权服务器 **MUST** 支持并强制校验(RFC 9700 §2.1.1) |
| 客户端不校验 `iss` | mix-up:把诚实 AS 的码发到攻击者 AS 的令牌端点 | 客户端 **MUST** 存 issuer 并比对,不符即中止(RFC 9700 §4.4.2.1、RFC 9207) |

本 demo 把这三点连同授权码的一次性/10 分钟生命周期、`state` 一次性、PKCE 降级检测,一起实现成两台"授权服务器 + 一个客户端"的状态机。

## 原理详解

**1. 流程骨架(RFC 6749 §4.1 步骤 A–E)**

```
客户端 --(A) 授权请求:response_type=code, client_id, redirect_uri, state, code_challenge--> 授权端点
授权端点 --(B) 认证资源所有者,决定授予/拒绝
授权端点 --(C) 302 到 redirect_uri?code=...&state=...&iss=...--> 客户端
客户端   --(D) POST 令牌端点:grant_type=authorization_code, code, redirect_uri, code_verifier--> 令牌端点
令牌端点 --(E) 校验客户端、码、redirect_uri 一致性后发 access_token(+ refresh_token)--> 客户端
```

**2. PKCE 为什么能挡住授权码截获(RFC 7636 §4.2 / §4.6)**

```
code_verifier  = 32 字节随机 → base64url → 43 字符(字符集 43*128 unreserved)
code_challenge = BASE64URL-ENCODE(SHA256(ASCII(code_verifier)))      # S256
服务端比对:      BASE64URL-ENCODE(SHA256(ASCII(收到 verifier))) == 授权时记录的 challenge
```

关键在**授权请求里只出现 challenge**(哈希),`code_verifier` 直到换令牌才发出。攻击者即使截获重定向里的 `code`(例如通过 Referer、恶意 SDK、自定义 scheme 劫持),也拿不到 verifier,兑换必然 `invalid_grant`。附录 B 给出了黄金向量:

```
verifier : dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk
challenge: E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM
```

本 demo 的 Python/JS 两侧都用这组向量做**字符串级**断言(比"自算自比"强得多)。

**3. 降级攻击与"只在两边都出现才成立"的校验(RFC 9700 §4.8.2)**

PKCE 的校验有一个容易被忽略的不对称:**只有授权请求带了 `code_challenge`,令牌端点才应接受 `code_verifier`**。否则服务端可以(错误地)在"没建 PKCE 会话"的情况下接受任何 verifier,降级就成立了。本实现直接把这个条件写成 `invalid_grant`。

**4. mix-up(RFC 9700 §4.4.2.1)**

客户端要与两个 AS 交互时,攻击者可诱导客户端把 A 的授权请求发到 B、再把 B 的码交给 A 的令牌端点。防御是 **RFC 9207 的 `iss` 参数**:客户端在发起时存下"这次请求发给了谁",回调时比对 `iss`(以及令牌端点的归属),不符立刻中止 —— 而不是"顺手拿码去换令牌"。

## 对比:三种"防护姿势"的强弱

| 姿势 | 防授权码截获 | 防 CSRF | 备注 |
| --- | --- | --- | --- |
| 仅 `state` | ✗ | ✓ | 传统做法;`state` 泄漏后还可被复用,故 §4.2.4 要求"首次使用后失效" |
| 仅 PKCE | ✓ | ✓(等价) | RFC 9700 §2.1:确认 AS 支持 PKCE 时**可以**用它代替 CSRF 令牌 |
| PKCE + `state` + `iss` | ✓ | ✓ | 公共客户端 + 多 AS 场景的实际底线 |

被弃用的姿势:隐式授予(`response_type=token`,§2.1.2 SHOULD NOT)、资源所有者密码凭据授予(§2.4 **MUST NOT**)、把令牌放进 URI 查询参数(§4.3.2 **MUST NOT**)。

## 环境

- Python 3.9+(仅标准库 `base64`/`hashlib`/`hmac`)
- Node.js 18+(`node:crypto`,用到 ESM 顶层 await)

## 运行方式

```bash
python oauth_pkce_check.py     # Python 侧 32 项断言
node   oauth_pkce.mjs          # JavaScript 侧 16 项断言(含 RFC 7636 附录 B 向量)
```

## 关键代码

```python
# RFC 7636 §4.6:服务端的唯一判定
expected = verifier if code["method"] == "plain" else s256(verifier)
if not hmac.compare_digest(expected, code["challenge"]):
    raise OAuthError("invalid_grant", "code_verifier mismatch")

# RFC 9700 §4.8.2:降级检测 —— 授权时没有 challenge 就不该收到 verifier
if not code["challenge"] and verifier is not None:
    raise OAuthError("invalid_grant", "code_verifier without code_challenge (downgrade)")
```

## 性能边界

- 纯内存状态机,单次流程微秒级;断言脚本整体 < 0.1 s。**无网络、无加密库**,不涉及真实 TLS/签名。
- `hmac.compare_digest` 用于避免 verifier 校验的时序侧信道 —— 真实服务端上哈希比较也是同样要求。

## 注意事项与常见坑

- **`code_challenge_method` 缺省是 `plain`**(RFC 7636 §4.3)。服务端若不显式要求 `S256`,等于默认接受明文挑战 —— 而 `plain` 下 challenge 就是 verifier,截获即失守(§7.2)。本实现的 `authorize()` 保留 `plain` 分支只为演示语义,**生产应拒绝**。
- **精确匹配不等于"看起来一样"**:`https://client.example.com/cb.evil.com`、大小写不同的主机名、带尾斜杠的变体都必须拒绝。本 demo 用三个用例分别钉住。
- **`error` 响应也必须带 `state`**(RFC 6749 §4.1.2.1),否则客户端分不清"被拒绝"和"被 CSRF 注入的错误响应"。
- **URI 校验失败时不要重定向**(§3.1.2.4 MUST NOT):一旦"尽力而为"地跳到未注册地址,授权码就送到了攻击者手上。本实现直接抛错。
- **重放要连带撤销**(RFC 9700 §4.2.4):同一 `code` 第二次兑换时,应当撤销第一次签发的那批令牌 —— 否则"码泄漏 + 已兑换"就无声无息了。
- 本 demo 不做 JWKS/签名校验、`expires_in` 到期吊销、刷新令牌轮换(§2.2.2 要求公共客户端 MUST 用发送方约束或轮换),这些属于令牌生命周期管理,已列在目录 README 的待研究清单里。

## 参考资料(实际联网读过)

- RFC 6749 *The OAuth 2.0 Authorization Framework* — <https://www.rfc-editor.org/rfc/rfc6749.html>(§4.1 授权码流程、§4.1.2 生命周期、§3.1.2.3/§3.1.2.4 URI 校验、§5.2 错误码)
- RFC 7636 *Proof Key for Code Exchange by OAuth Public Clients* — <https://www.rfc-editor.org/rfc/rfc7636.html>(§4.1 verifier 语法、§4.2 变换、§4.6 校验、§7.2 降级、附录 A/B 向量)
- RFC 9700 *Best Current Practice for OAuth 2.0 Security* — <https://www.rfc-editor.org/rfc/rfc9700.html>(§2.1/§2.1.1/§2.4/§2.6、§4.2.4、§4.3.2、§4.4.2/§4.4.2.1/§4.4.2.2、§4.8.2)
