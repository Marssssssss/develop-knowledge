# DPoP 与发送方约束令牌(Sender-Constrained Tokens)

> OAuth 2.0 的默认令牌是 **Bearer** —— RFC 6750 的原话是"任何持有它的任何一方
> (any party in possession of it)都能使用"。令牌一旦从 HTTPS 里漏出去(XSS、
> 日志、代理、Referer),攻击者不需要任何密钥就能顶替用户。
>
> 发送方约束要解决的问题只有一句:**让令牌自己带上"谁才能用我"**。DPoP(RFC 9449)
> 用"客户端自持的一把非对称钥匙"来做到这件事,而 RFC 8705 用"TLS 客户端证书"来做。

## 1. 简介

本 demo 实现 RFC 9449 的完整校验链路(§4.3 的 12 条)与 RFC 8705 的证书绑定判定,
并显式对比三种令牌:

| 令牌形态 | 攻击者拿到令牌后 | 绑定对象 | 需要的基础设施 |
| --- | --- | --- | --- |
| Bearer(RFC 6750) | **直接用** | 无 | 无 |
| DPoP(RFC 9449) | 用不了(没有私钥) | 客户端公钥指纹 `jkt` | 无(纯应用层) |
| mTLS 绑定(RFC 8705) | 用不了(没有证书私钥) | X.509 证书指纹 `x5t#S256` | 双向 TLS 到 RS |

## 2. 原理详解

### 2.1 DPoP proof 是一个"短命的、绑死这一次请求"的 JWT

RFC 9449 §4.2 要求:

| 位置 | 字段 | 要求 |
| --- | --- | --- |
| JOSE 头 | `typ` | 必须是 `dpop+jwt`(§3.11 RFC 8725 的显式类型化) |
| JOSE 头 | `alg` | **必须是已注册的非对称算法**,`MUST NOT` 为 `none` 或 MAC |
| JOSE 头 | `jwk` | 客户端公钥,**`MUST NOT` 含私钥** |
| payload | `jti` | ≥96 bit 随机,供服务器做重放检测 |
| payload | `htm` | HTTP 方法 |
| payload | `htu` | 目标 URI,**不含 query 与 fragment** |
| payload | `iat` | 创建时间 |
| payload | `ath` | 访问资源时**必须**:`base64url(SHA-256(ASCII(access_token)))` |
| payload | `nonce` | 服务器下发过 nonce 时**必须** |

关键设计取舍(§4.2 原文):**DPoP 只签方法与 URI,不签 body 与其他头**。
理由是要避开"HTTP 消息归一化"这个泥潭。代价写在 §11.7 ——
DPoP **不提供请求完整性**,它只证明"这个请求是这个私钥持有者发出的"。

### 2.2 §4.3 的 12 条校验(本 demo 完整实现)

```text
E1  DPoP 头不能多于一个          E7  jwk 不得含私钥成员
E2  header 值是单个合法 JWT      E8  htm == 当前请求方法
E3  必需 claim 齐全              E9  htu == 当前 URI(忽略 query/fragment)
E4  typ == dpop+jwt              E10 nonce 匹配服务器近期下发的值
E5  alg 是可接受的非对称算法     E11 iat 落在可接受窗口内(秒~分钟级)
E6  签名用 jwk 里的公钥验过      E12 ath 哈希一致 + 证明公钥 == 令牌绑定的公钥
```

### 2.3 `ath` 到底防住了什么(§7)

这是最容易讲错的一条。设想客户端用**同一把钥匙**跟 AS 说话,手里有两个资源
所有者的令牌 `AT1` / `AT2`:

```text
抓到给 AT1 的 proof  →  换成 AT2 重放
  ├─ 没有 ath:服务器只看签名与 jkt,**验过**,攻击者把 AT1 的权限换成 AT2 的
  └─ 有  ath:ath 是 AT1 的哈希,与 AT2 的哈希不符 → E12a 拒绝
```

自检里 `E12a proof 换到另一个访问令牌上被拒` 就是这一条。注意 §7 原文还强调:
**`ath` 本身不防重放**,它只做"令牌—证明"绑定;防重放靠 `jti` 单用 + 时间窗口。

### 2.4 nonce:把证明的寿命压到"服务器说了算"(§8 / §9)

没有 nonce 时,能控制客户端的人(包括终端用户自己)可以**预生成任意远未来的
DPoP proof**(§8 原文)。nonce 机制:

- AS 缺 nonce → **400** + `{"error":"use_dpop_nonce"}` + `DPoP-Nonce` 头
- RS 缺 nonce → **401** + `WWW-Authenticate: DPoP error="use_dpop_nonce"` + `DPoP-Nonce`
- 更省一次往返的办法:在**上一次 200 响应**里就带上新的 `DPoP-Nonce`(必须 `no-store`)
- **AS 与 RS 的 nonce 是两套、彼此独立**(§9 末段),自检里用"AS 的 nonce 拿到 RS 上被拒"固定这一点
- 浏览器端 CORS 只能读到安全列表响应头,所以服务端必须把 `DPoP-Nonce` 加进
  `Access-Control-Expose-Headers`(§8 原文)

### 2.5 与 mTLS 证书绑定(RFC 8705)的对比

| 维度 | DPoP | mTLS 绑定 |
| --- | --- | --- |
| 绑定值 | `cnf.jkt` = RFC 7638 JWK SHA-256 指纹 | `cnf["x5t#S256"]` = `base64url(SHA-256(DER))`,**去尾随 `=`** |
| 失败响应 | 401 + `WWW-Authenticate: DPoP ...` | 401 + `invalid_token`(按 RFC 6750) |
| 覆盖范围 | 应用层,可穿过 TLS 终止/反向代理 | TLS 层;§6.5 明确指出**TLS 终止处必须把证书信息传给后端**,否则后端验不了 |
| 前置约束 | 无 | §3:是否要求互 TLS **不能由访问令牌决定** —— 令牌是 TLS 之上的 Application Data,而 `CertificateRequest` 在握手阶段就发出了 |

## 3. 关键代码

**RFC 7638 指纹(字典序 + 无空白 + 无填充)**:

```python
def jwk_thumbprint(jwk):
    canonical = json.dumps(jwk, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return b64u(hashlib.sha256(canonical).digest())
```

`jwk` 里成员的**顺序与空白**直接参与哈希 —— 自检用 RFC 9449 Figure 9 的官方值
`0ZcOCORZNYy-DWpqq30jZyJGHTN0d2HglBV3uiguA4I` 反向验证了这个实现。

## 4. 运行方式

```bash
cd DPoP与发送方约束令牌
python selfcheck_dpop.py     # 35 条断言,输出 "ALL OK"
go run .                     # Go 侧同样一组断言
```

## 5. 性能与工程边界

- **jti 单用检查的代价**:§11.1 建议"在证明的有效窗口内"缓存 `jti`。窗口 60 秒、
  每客户端 10 RPS → 单资源服务器需缓存 600 个 `jti`。这是**有状态**的,
  与 Bearer 的无状态校验相比是实打实的架构变化。
- **nonce 需要共享存储**:多副本部署时"近期 nonce 窗口"必须共享,否则客户端
  从副本 A 拿到 nonce、打到副本 B 会被拒(§8 说这种不一致是"自愈的"——
  重试即可,但会推高错误率)。
- **签名验算开销**:每个请求都要一次非对称验签。ES256 在服务端约几十微秒级,
  但比 HMAC/JWT 验签贵一个数量级,高 QPS 场景要考虑缓存(按 `jti` 缓存验签结果)。

## 6. 注意事项与常见坑

1. **DPoP 不防"客户端被完全控制"**:§11.4 明确写到,客户端上下文里的不可信代码
   (XSS)仍然可以直接调用同一个私钥生成 proof。DPoP 抗的是**令牌泄露后的重放**,
   不是端点被攻陷。
2. **nonce 降级攻击(§11.3)**:攻击者可以剥掉 `DPoP-Nonce` 让服务器回 401,
   诱使客户端改用不带 nonce 的 proof。服务器必须**强制**要求 nonce,不能"有就验、没有就算了"。
3. **`htu` 比较要先归一化**:§4.3 末段要求做 RFC 3986 §6.2.2 语法归一化与
   §6.2.3 方案归一化,否则 `https://a:443/x` 与 `https://a/x` 会被判成不匹配。
4. **签名算法口径**:本 demo 为了让"签—验"在本机无第三方库时真实可跑,内置了
   **教科书式 RSA-FDH**(256-bit 素数,`sig = H(m)^d mod n`),`alg` 仍标 `RS256`。
   它只承担"持有私钥才能产出、公钥可验"的语义,**不提供生产强度**;
   生产实现应按 RFC 9449 §11.6 用 ES256 / EdDSA。
5. **不要把 `jwk` 换成 `kid`**:DPoP 要求公钥**内嵌**,服务器不需要也不应该
   去客户端注册信息里查公钥 —— 这是它能做到"零预注册"的原因。
6. **mTLS 下别忘 TLS 终止**:§6.5 是运维事故高发点 —— LB 上做了 TLS 终止又不
   透传客户端证书指纹,后端拿到的永远是 LB 的证书,校验必然失败或必然通过。

## 7. 参考资料(本 demo 实际读取)

- RFC 9449 — *OAuth 2.0 Demonstrating Proof of Possession (DPoP)*
  <https://www.rfc-editor.org/rfc/rfc9449.txt>
- RFC 8705 — *OAuth 2.0 Mutual-TLS Client Authentication and Certificate-Bound Access Tokens*
  <https://www.rfc-editor.org/rfc/rfc8705.txt>
- RFC 7638 — *JSON Web Key (JWK) Thumbprint* <https://www.rfc-editor.org/rfc/rfc7638.txt>
- RFC 6750 — *The OAuth 2.0 Authorization Framework: Bearer Token Usage*
  <https://www.rfc-editor.org/rfc/rfc6750.txt>
