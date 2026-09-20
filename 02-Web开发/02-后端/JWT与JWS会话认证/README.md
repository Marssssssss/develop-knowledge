# JWT 与 JWS 会话认证（RFC 7519 / RFC 7515）

JWT 本身**不是**一个认证方案，它只是一个「声明的容器」；安全性完全来自外层的 JWS 签名与调用侧的校验纪律。本 demo 把 RFC 7515 的紧凑序列化、签名输入构造与 RFC 7519 的注册声明校验做成可执行模型，并拿 RFC 附录的**官方向量**做字节级对拍。

## 一、紧凑序列化：三段，两个句点

```text
BASE64URL(UTF8(JWS Protected Header)) '.' BASE64URL(JWS Payload) '.' BASE64URL(JWS Signature)
```

**签名输入（JWS Signing Input）是 `ASCII("header.payload")`**——即紧凑表示中第二个句点**之前**（不含第二个句点）的那一段 ASCII 字节。这是最容易被手写实现搞错的地方：签名算的是**编码后**的两段，不是原始 JSON。

## 二、base64url：不是标准 base64

- 用 `-` 和 `_` 代替 `+` 和 `/`
- **没有 padding**（不补 `=`）

实测：标准 base64 的 `+//+` 在 base64url 里是 `-__-`；三字节输入恰好 4 个字符、不含 `=`。很多「JWT 校验偶发失败」的根因就是某一侧补了 padding 或用了标准字母表。

## 三、RFC 7515 附录 A.1 的官方向量

```text
Protected Header   {"typ":"JWT",<CR><LF> "alg":"HS256"}
                   → eyJ0eXAiOiJKV1QiLA0KICJhbGciOiJIUzI1NiJ9
Payload            {"iss":"joe",<CR><LF> "exp":1300819380,<CR><LF>
                    "http://example.com/is_root":true}
                   → eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6
                     Ly9leGFtcGxlLmNvbS9pc19yb290Ijp0cnVlfQ
Signature          dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk
```

注意 header 的**字节序列是确定的**：第一行无首尾空格、两行之间是 CRLF（13, 10）、第二行有一个前导空格（32）、末行没有结尾换行。规范专门用 JSON 数组形式列出了这 30 个字节——因为不同平台的换行/缩进会产生不同的 base64，从而不同的签名。本模型用官方字节序列重算 HMAC-SHA256，结果与官方签名**逐字符相同**。

密钥以 JWK 形式给出（`kty: oct`，`k` 本身也是 base64url），实测解出 64 字节。

## 四、RFC 7519 §4.1 注册声明的校验语义

| 声明 | 语义 | 边界 |
| --- | --- | --- |
| `exp` | 「current date/time **MUST be before** the expiration date/time」 | **严格小于**：`now == exp` 就已经过期 |
| `nbf` | 「current date/time **MUST be after or equal to**」 | **大于等于**：`now == nbf` 通过 |
| `iat` | 签发时间，可用于判断 token 年龄 | — |
| `jti` | 唯一 ID，可用于防重放 | 大小写敏感 |
| `iss` | 签发者 | 处理方式是应用相关的 |
| `sub` | 主体 | 必须在 issuer 上下文内局部唯一或全局唯一 |
| `aud` | 受众 | 见下 |

`exp` 与 `nbf` 都允许「some small leeway, usually no more than **a few minutes**」来吸收时钟偏移——注意是「几分钟」，不是无上限。

## 五、`aud`：缺席即 MUST be rejected

> Each principal intended to process the JWT MUST identify itself with a value in the audience claim. **If the principal processing the claim does not identify itself with a value in the "aud" claim when this claim is present, then the JWT MUST be rejected.**

两条实现要点：

1. `aud` 一般是**字符串数组**；「In the special case when the JWT has one audience, the aud value MAY be a single case-sensitive string」——所以校验时必须同时兼容两种形态。
2. 只要 token 里**有** `aud`，而处理方拿不出自己的标识（或标识不在其中），就**必须拒绝**。实测：不传 `audience` 参数去校验一个带 `aud` 的 token，会被拒。

## 六、`alg: none` 与算法白名单

RFC 7515 明确定义了**无保护 JWS**（Unsecured JWS）：

> A JWS that provides no integrity protection. Unsecured JWSs use the "alg" value "none".

附录 A.5 的例子：`{"alg":"none"}` → `eyJhbGciOiJub25lIn0`，**签名是空字节串**，所以整个紧凑表示**以句点结尾**：

```text
eyJhbGciOiJub25lIn0.eyJpc3MiOiJqb2UiLA0KICJleHAiOjEzMDA4MTkzODAsDQogImh0dHA6
Ly9leGFtcGxlLmNvbS9pc19yb290Ijp0cnVlfQ.
```

实测对照：

| 情形 | 结果 |
| --- | --- |
| `alg=none`、签名段为空，白名单只含 `HS256` | **拒绝** |
| 同一 token，白名单显式含 `none` | **通过** |
| `alg=none` 但签名段非空 | **拒绝**（规范：签名必须是空字节串） |

结论：**校验侧必须硬编码算法白名单**，绝不能「读 header 里的 alg 来决定用什么算法验」。同理，把 RS256 的公钥当 HMAC 密钥去验 HS256 的算法混淆攻击，靠的也是同一条疏漏。

## 七、运行方式

```bash
python selfcheck_jwt.py     # 37 项断言
```

## 八、关键代码

- `main.py` — `b64url_encode/decode`、`signing_input`、`sign_hs256`、`serialize/parse_compact`、`verify_hs256`（带 `allowed_algs`）、`validate_claims`（exp/nbf/aud/iss + leeway）
- `jwt.go` — 同构 Go 实现，用 `crypto/hmac` + `base64.RawURLEncoding` 复现官方向量
- `selfcheck_jwt.py` — 官方向量对拍 + 误报集/漏报集

## 九、注意事项与常见坑

1. **签名算的是编码后的两段，不是原始 JSON**。
2. **base64url 无 padding**；与标准 base64 混用会偶发失败。
3. **`exp` 是严格小于**，`nbf` 是大于等于——两者的边界方向相反。
4. **`aud` 必须校验**，且要兼容单值字符串与数组两种形态。
5. **算法白名单必须写死在校验侧**，不能信任 header。
6. **`alg: none` 是规范里的一等公民**，不是「非法值」，只能靠白名单挡。
7. **JWT 不可撤销**：`exp` 之外还需要短期有效期 + 刷新令牌 / 黑名单。
8. **不要把敏感数据放 payload**——它只是 base64 编码，不是加密。需要保密请用 JWE（RFC 7516）。
9. **时钟偏移只允许几分钟的 leeway**，别为了"保险"设成几小时。
10. `iss`/`sub` 的处理方式是**应用相关**的，规范只规定了值的形态（StringOrURI）。

## 十、参考资料

- RFC 7515 — JSON Web Signature (JWS) — <https://www.rfc-editor.org/rfc/rfc7515.txt>（§5.2 签名输入、§7.1 紧凑序列化、附录 A.1 HMAC 示例、A.5 无保护 JWS）
- RFC 7519 — JSON Web Token (JWT) — <https://www.rfc-editor.org/rfc/rfc7519.txt>（§4.1 注册声明名、§4.1.3 aud、§4.1.4 exp、§4.1.5 nbf、§4.1.6 iat、§4.1.7 jti、§5 JOSE Header）
