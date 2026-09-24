# JWT 访问令牌 profile（RFC 9068）与受众校验

OAuth 2.0 原本不规定访问令牌的格式。但现实中大量实现用 JWT，于是 RFC 9068 出了一个
标准 profile：规定**头怎么填、哪七个声明必须有、资源服务器按什么顺序校验**。
其中最容易被忽略、也最容易出事的是 `typ` 与 `aud`。

## 一、`typ` 必须显式声明为 `at+jwt`（§2.1）

```json
{ "typ": "at+jwt", "alg": "RS256" }
```

- JWT 访问令牌 **MUST** 被签名，**MUST NOT** 使用 `none`。
- 符合本规范的 AS 与 RS **MUST** 把 **RS256** 列入支持的签名算法。
- 注册的媒体类型是 `application/at+jwt`，**MUST** 写进 `typ` 头参数；按 RFC 7515 §4.1.9
  的建议省略 `application/` 前缀，所以 `typ` **SHOULD** 就是 `at+jwt`。

**为什么非要这个头**：JWT 访问令牌的数据布局和 OIDC 的 `id_token` 长得几乎一样。
没有显式类型，资源服务器就可能把客户端拿来的 **ID Token 当访问令牌接受**——
ID Token 的 `aud` 是 client_id 而不是资源，但这正是很多实现不校验 `aud` 的原因。
§5 把这个威胁叫 cross-JWT confusion。

本 demo 断言：`typ` 为 `JWT`（ID Token 的写法）或缺失时，一律拒绝。

## 二、七个 REQUIRED 声明（§2.2）

`iss` / `exp` / `aud` / `sub` / `client_id` / `iat` / `jti` —— **七个全部 REQUIRED**。

其中两个是 JWT 通用实现里最容易漏的：

- **`client_id`**：访问令牌是**发给客户端**的，光有 `sub`（资源拥有者）不够。
- **`jti`**：令牌唯一标识，是撤销/单次使用/审计的抓手。

`sub` 的口径按授权类型分叉：

| 授权类型 | `sub` SHOULD 是 |
| --- | --- |
| 涉及资源拥有者（授权码等） | 资源拥有者的 subject identifier |
| 不涉及资源拥有者（客户端凭证等） | AS 用来指代**客户端应用**的标识 |

可选声明：`auth_time` / `acr` / `amr`（§2.2.1，在同一授权响应派生出的所有令牌里
**取值固定不变**）；`scope`（§2.2.3，授权请求带 scope 时 SHOULD 有）。

## 三、资源服务器的六步校验（§4）

| 步 | 检查 | 要点 |
| --- | --- | --- |
| 1 | `typ` | 必须是 `at+jwt` 或 `application/at+jwt`，其他一律拒 |
| 2 | 解密 | 注册时协商了加密而收到的未加密 → SHOULD 拒 |
| 3 | `iss` | 与 AS 的 issuer 标识 **MUST 精确相等** |
| 4 | `aud` | **MUST** 含一个「资源服务器认为属于自己的」资源指示值 |
| 5 | 验签 | 按 `alg` 验（RFC 7515）；`alg=none` 必拒；**MUST** 只用 AS 提供的密钥 |
| 6 | `exp` | 当前时间 **MUST 早于** `exp`；MAY 给几分钟时钟偏移余量 |

任一步失败，按 RFC 6750 §3.1 返回 **`invalid_token`**。

几个本 demo 断言到位的细节：

- **`iss` 是精确字符串比较**：`https://as.example.com/` 与 `https://as.example.com`
  不相等；大小写不同也不相等；尾随空格也不相等。
- **`exp` 是严格小于**：当前时间**恰好等于** `exp` 时已经失效（`now >= exp` 即拒）。
- **`aud` 可以是数组**：只要数组里含自己的资源指示值就通过；不含就拒。
- **顺序可审计**：`aud` 检查在验签**之前**，所以伪造签名的令牌先撞 `typ`/`aud` 而不是
  签名错误。本 demo 把检查顺序做成显式的 `CHECK_ORDER`，并断言「typ 错 + aud 错时报 typ」。

## 四、跨 JWT 混淆（§5）

> authorization servers MUST use a distinct identifier as an "aud" claim value to
> uniquely identify access tokens issued by the same issuer for distinct resources.

同一个 AS 发往资源 A 和资源 B 的令牌，**`aud` 必须互不相同**。否则 A 收下的令牌
可以被拿去 B 用。本 demo 用两个资源服务器交叉验证：A 的令牌被 B 拒、B 的令牌被 A 拒。

另一条是 `sub` 混淆：若 AS 让客户端凭证模式下的 `sub` 取 `client_id`，就必须阻止客户端
注册**任意** `client_id`——否则恶意客户端可以注册成高权限资源拥有者的标识，去迷惑依赖
`sub` 做授权判断的资源服务器。

## 五、未建模项

- **§2.2.3 的 "每个 scope 串 MUST 对 `aud` 所指资源有意义"**：这条是签发侧的约束，
  资源服务器侧无法独立判定（需要知道 scope 到资源的映射表），本 demo **未断言**。
- 真实的签名验证（JWKS 获取、`kid` 选择、`x5t#S256` 指纹绑定）未建模，
  `validate()` 用 `signature_ok` 参数替代。
- §4 提到的 "MAY provide for some small leeway, usually no more than a few minutes"
  未给出具体数值，本 demo 的 `leeway` 是可配参数，**不声称任何默认值是官方推荐**。

## 代码结构

| 文件 | 内容 |
| --- | --- |
| `jwtat.py` | 七个 REQUIRED 声明 + §4 六步校验 + 跨 JWT 混淆对照 |
| `jwtat.go` | 同模型的 Go 转写 |
| `selfcheck_jwtat.py` | Python 自检（实跑 **49** 断言） |
| `selfcheck_jwtat.go` | Go 自检（同套断言） |

## 参考资料（实际读过）

- RFC 9068《JSON Web Token (JWT) Profile for OAuth 2.0 Access Tokens》—
  `https://www.rfc-editor.org/rfc/rfc9068.txt`（35141 B 全文实读）
  §2.1 Header、§2.2 Data Structure（含 §2.2.1 认证信息声明、§2.2.3 授权声明）、
  §4 Validating JWT Access Tokens、§5 Security Considerations
- 文中引用的 RFC 7519（JWT）、RFC 7515（JWS）、RFC 6750 §3.1（invalid_token）、
  RFC 8693 §4.3（client_id）、RFC 8725 §2.8（cross-JWT confusion）
