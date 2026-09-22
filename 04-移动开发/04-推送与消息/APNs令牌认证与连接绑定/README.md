# APNs 令牌认证与连接绑定

> provider token 是 APNs 的**无状态**认证方式：每个请求自带一个 ES256 签名的 JWT，APNs 不必再去查证书。代价是**令牌有寿命、连接会绑死**——这两件事是推送服务端最容易踩的坑，也是本篇的全部内容。
>
> 来源：Apple 官方《Establishing a token-based connection to APNs》《Sending notification requests to APNs》《Handling notification responses from APNs》三篇 DocC 全文实读；JWT/ES256 部分对照 RFC 7515 / 7518 / 6979。

## 一、令牌只有四个键值对

官方原话是"the following table describes the four key-value pairs the token itself contains"——header 两个、claims 两个：

```json
{ "alg": "ES256", "kid": "ABC123DEFG" }
{ "iss": "DEF123GHIJ", "iat": 1437179036 }
```

| 位置 | 键 | 含义 |
| --- | --- | --- |
| header | `alg` | 恒为 `ES256`（P-256 + SHA-256） |
| header | `kid` | 密钥 ID，开发者后台申请 `.p8` 时给的 10 位串 |
| claims | `iss` | **Team ID**，不是 bundle ID |
| claims | `iat` | 令牌生成时刻（UNIX 秒） |

签名输入是 `ASCII("<base64url(header)>.<base64url(claims)>")`，签名值是 `r || s` 各 32 字节定长共 64 字节，最后拼成紧凑序列化的三段式。请求头写成：

```
authorization: bearer eyJhbGciOiJFUzI1NiI...
```

注意 `bearer` 是**小写**（官方原样如此）。

### 官方文档里的示例令牌不能当模板

官方示例给的令牌解出来是这样的（`selfcheck` §2 有断言）：

```
header = { "kid": "8YL3G3RRX7" }                       ← 没有 alg
claims = { "iss": "C86NV9JX3D", "iat": "1459143580650" } ← iat 是**字符串**，还是**毫秒**
```

它只是示意。**真正要发的令牌必须带 `alg: ES256`**，`iat` 按正文表格理解为 UNIX 秒（"the token generation time"，超过一小时即失效）。把示例里那个 13 位毫秒串直接当秒用，得到的令牌年龄是四万多年。

## 二、刷新窗口是 [20 分钟, 60 分钟]，不是"越勤越好"

官方三条约束：

1. `iat` 距今**超过 1 小时** → APNs 拒绝，回 `403 ExpiredProviderToken`；
2. 同一条连接上**换新令牌快于 20 分钟** → `429 TooManyProviderTokenUpdates`（reason 释义原文："Update the authentication token no more than once every 20 minutes."）；
3. 建议的刷新节奏是 **不少于 20 分钟、不多于 60 分钟一次**。

这三条合起来意味着：**每条连接都要单独记录上一次令牌的 `iat`**，不能用一个全局令牌池往所有连接上怼。本 demo 的 `Connection` 正是这么建模的：

```python
if iat != self.last_iat and 0 <= iat - self.last_iat < 1200:
    return self._reject(429, 'TooManyProviderTokenUpdates')
```

注意判据是**严格大于 3600 秒**：`iat` 年龄恰好 3600 秒仍在窗口内（`selfcheck` §4 有这条边界断言）。

## 三、首推之后，连接被绑死

这是最反直觉的一条。官方原话：

> As part of the authentication process, APNs relates a team ID and associated bundle IDs to a connection **on the first push**. After the authentication process, attempting to associate a different team or sending a push to a newly added bundle ID causes an error.

也就是说连接不是"无状态的管道"，它在第一次推送时就固化了三样东西：**团队、环境、可推的 topic 集合**。之后：

| 后续动作 | reason | HTTP |
| --- | --- | --- |
| 换成另一个开发者账号的令牌 | `Forbidden` | 403 |
| 换用不同环境的钥匙（sandbox ↔ production） | `BadEnvironmentKeyIdInToken` | 403 |
| 换用别的 key ID（且不是 related key） | `UnrelatedKeyIdInToken` | 403 |
| 推一个不在首推关联集合里的 topic | `TopicDisallowed` | 403 |

`UnrelatedKeyIdInToken` 的官方释义很直白："To use this token, **open a new connection**."——别挣扎，重连。

还有两条运维向的：

- **一个开发者账号一条连接池**："You can't map a connection to APNs to multiple teams."
- **App 过户后必须重连**："For transferred apps, close and recreate any existing connections... If you don't, APNs can't start accepting push requests from the new team."

### 两种钥匙的作用域决定了首推之后还能换什么

| | team-scoped | topic-specific |
| --- | --- | --- |
| 可用 topic | 本团队**任意** | 只限登记的 topic |
| 环境 | 限定单一环境 | 限定单一环境 |
| 数量上限 | **每环境 2 把** | 沙盒 200 + 生产 **200** |
| 单把 topic 上限 | — | **400** 个 |
| related key | — | 同环境内最多 **1 把** |

首推用什么钥匙，决定了后面能换什么：

- **首推用 team-scoped**：之后用 topic-specific 钥匙、或用另一个环境的 team-scoped 钥匙 → 报错；
- **首推用 topic-specific**：之后用不相关的 topic-specific 钥匙、别的环境的钥匙、team-scoped 钥匙、或推未关联的 topic → 报错；
- **唯一例外是 related key**：官方说"you can use authentication tokens from both the topic-based key and its related key (if available)"。

> **口径声明**：官方只说 related key 的令牌"能用"，没有明说两把钥匙的 topic 集合是否取并集。本 demo 取**并集**（`allowed_topics()`），并在 §5 断言里显式标注了这一点。

## 四、无状态 ≠ 无代价

官方对 token 与证书方式的比较里有一句容易被忽略：

> Token-based requests are slightly larger than certificate-based requests because each request contains the token.

以及它要求"update and encrypt your tokens **at least once an hour**"。所以无状态省掉的是 APNs 侧查证书的开销，换来的是**服务端侧必须有一个按连接维度管理的令牌刷新器**：不到 20 分钟不许换、超过 60 分钟必须换、每次换还要走 ES256 签名。

## 五、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/p256.py` | P-256 素域运算 + ECDSA 签名验签，nonce 按 RFC 6979 确定性生成 |
| `python/apns_jwt.py` | JWT 分段、base64url（无填充）、ES256 绑定、`authorization` 头 |
| `python/apns_token.py` | 密钥作用域配额 + 刷新窗口 + 连接绑定状态机 |
| `python/main.py` | 四段演示输出 |
| `python/selfcheck_apnstoken.py` | 66 条断言（含 RFC 6979 官方向量回环） |
| `go/apns_token.go` | 同一套策略的 Go 版（不含 ES256，生产用 `crypto/ecdsa`） |

跑起来：

```bash
cd python && python main.py && python selfcheck_apnstoken.py
```

### 为什么自己实现 P-256

本机环境没有 `cryptography`，而 demo 的说服力要求"签名是真的"。所以 `p256.py` 用不到 200 行实现了点加/倍点/标量乘与 ECDSA，nonce 严格照抄 RFC 6979 §2.3.2~§3.2，并用 **RFC 6979 A.2.5 的 P-256/SHA-256/`"sample"` 向量**端到端校验：

```
Ux = 60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6
Uy = 7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299
r  = EFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716
s  = F7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8
```

四个值全部逐字节吻合，**通过 ≠ 只是跑通**。

## 六、容易写反的三处

1. **`bits2int` 是截左不是截右**：`blen > qlen` 时保留**左起** `qlen` 位（右移 `blen-qlen`）。写成"取低 256 位"会得到完全不同的 nonce，而且不会报错——只会在对端验签失败。
2. **`bits2octets` 要先模 q 再定长**：`z2 = z1 mod q`，然后才 `int2octets`。直接把 `bits2int` 的结果拿去定长编码，同样静默地错。
3. **刷新判据的比较基准是"上一次的 iat"而不是"上次刷新时刻"**：APNs 拿到的是令牌里的 `iat`，服务端如果按自己的本地签发时刻记账，在时钟漂移下会算错间隔。

## 参考资料（实际读过的来源）

- [Establishing a token-based connection to APNs](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns) — 令牌四键值对、刷新窗口、钥匙作用域与配额、连接绑定规则（DocC JSON 全文实读）
- [Sending notification requests to APNs](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns) — `authorization: bearer <token>` 头格式与示例令牌
- [Handling notification responses from APNs](https://developer.apple.com/documentation/usernotifications/handling-notification-responses-from-apns) — `ExpiredProviderToken` / `TooManyProviderTokenUpdates` / `UnrelatedKeyIdInToken` / `BadEnvironmentKeyIdInToken` / `TopicDisallowed` 的状态码与释义
- [RFC 6979](https://www.rfc-editor.org/rfc/rfc6979) §2.3.2~§3.2、A.2.5 — 确定性 ECDSA 与 P-256 测试向量
- [RFC 7518](https://www.rfc-editor.org/rfc/rfc7518) §3.4 — ES256 与 `r || s` 的 64 字节签名编码
- [RFC 7515](https://www.rfc-editor.org/rfc/rfc7515) §2 — JWS 紧凑序列化与 base64url 无填充
