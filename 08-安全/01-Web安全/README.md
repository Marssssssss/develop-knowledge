# Web 安全

## OWASP Top 10(2021)

1. A01: 失效的访问控制(Broken Access Control)
2. A02: 加密机制失效(Cryptographic Failures)
3. A03: 注入(Injection)
4. A04: 不安全设计(Insecure Design)
5. A05: 安全配置错误(Security Misconfiguration)
6. A06: 脆弱和过时的组件(Vulnerable Components)
7. A07: 识别和认证失败(Identification & Authentication Failures)
8. A08: 软件和数据完整性失效(Software & Data Integrity)
9. A09: 安全日志和监控失败(Security Logging & Monitoring)
10. A10: 服务器端请求伪造(SSRF)

## 已完成 demo

### 第一批(请求侧基础攻防)

| 路径 | 知识点 | 语言 |
| --- | --- | --- |
| `XSS与CSP/` | XSS 三类型(Reflected/Stored/DOM-based)+ 5 大上下文编码 + CSP nonce 机制 + WAF 黑名单反模式 | Python + JavaScript |
| `CSRF-Token/` | CSRF Token 同步器令牌 + Signed Double Submit Cookie + SameSite + Fetch Metadata + Origin/Referer 兜底 | Python + JavaScript |
| `SQL注入与预编译/` | SQL 注入与参数化查询(sqlite3 prepared statements + 跨语言占位符对照) | Python + Go |
| `JWT验证/` | JWT HS256 验证(RFC 7519 §A.1 测试向量 + RFC 8725 Algorithm Confusion 防御 + jti 黑名单) | Python + Go |
| `Same-Origin与CORS/` | 同源策略(RFC 6454 三元组)+ CORS(Simple Request + Preflight + 凭据请求) | Python + JavaScript |

### 第二批(协议语义与浏览器侧状态)

| 路径 | 知识点 | 语言 |
| --- | --- | --- |
| `OAuth2-PKCE与授权码/` | 授权码流程 + PKCE(RFC 6749 §4.1 / RFC 7636 §4.6 + RFC 9700:精确匹配 redirect_uri、码单次+600s、降级检测、RFC 9207 `iss` 防 mix-up) | Python + JavaScript |
| `Cookie安全属性与SameSite/` | RFC 6265/6265bis 存储模型(域匹配、default-path、`__Host-`/`__Secure-` 大小写不敏感、非安全 cookie 不得覆盖 Secure cookie)+ SameSite 检索与 Lax-allowing-unsafe + HSTS(RFC 6797 §8) | Python + JavaScript |
| `SSRF与URL解析歧义/` | WHATWG URL §3.5 IPv4 解析(`0x7f.1`/`2130706433`/`0177.0.0.1`)+ 解析器分歧(`example.com\@evil.com`)+ 允许列表重建、DNS pinning、禁跟随重定向、内嵌 v4 解包 | Python + JavaScript(原生 WHATWG URL 对照) |
| `访问控制与IDOR/` | A01 失效的访问控制:RBAC/ABAC/ReBAC 三层叠加、对象级授权(CWE-639)、deny-overrides + 默认拒绝、会话级间接引用、失败安全退出与审计 | Python + Go |
| `点击劫持与框架嵌入/` | HTML §7.7 XFO 处理模型(官方"困惑多值"结果表)+ CSP `frame-ancestors` 优先与逐层祖先校验 + `<meta>` 无效 + frame-buster 绕过手法 | Python + JavaScript |

### 第三批(浏览器侧纵深防御)

| 路径 | 知识点 | 语言 |
| --- | --- | --- |
| `DPoP与发送方约束令牌/` | DPoP(RFC 9449 §4.3 的 12 条校验:typ/alg/jwk/htm/htu/iat/ath/nonce/jti 单用)+ `jkt` 公钥指纹绑定 + AS 400 / RS 401 两套独立 nonce + `dpop_jkt` 授权码绑定;对照 RFC 8705 的 `x5t#S256` 证书绑定 | Python + Go |
| `TrustedTypes与DOMXSS/` | W3C Trusted Types:§3.4 汇点只接受类型化值的四条分支、default policy 在强制/report-only 下的差异、§3.8 属性表(`on*`→TrustedScript)、§4.2.5 的 `'none'` 被忽略与 `'allow-duplicates'` | Python + Go |
| `CSP strict-dynamic与违规报告/` | CSP3 §6.7.3.2/§6.7.3.3 的内联匹配与 `'strict-dynamic'` 短路、§6.7.1.1 的 parser-inserted 判定、§6.7.2.6 `'none'` 的三条细节、§2.4 sample 截断 40 字符、§8.5 Strict CSP 判据 | Python + Go |
| `SRI与完整性策略/` | W3C SRI:标准 base64 摘要(非 base64url)、§3.3.3「只验最强那批」、§3.3.4 空集即放行与 CORS 要求、§3.8 Integrity-Policy 的 Dictionary 结构与 §3.8.2 判定顺序 | Python + Go |
| `COOPCOEP跨源隔离/` | HTML §7.1.3 五种 opener policy 与 BCG 切换判定、COEP §2.3 的 fail-open 表(`require-corp, require-corp` → `unsafe-none`)、§3.2.1 CORP internal check(`same-site` 的 https 约束)、§4.3 子文档必须自声明 | Python + Go |

## 待研究

- [x] XSS 攻击与 CSP 防御 ✓ (已完成,见上表 XSS与CSP)
- [x] CSRF Token 机制 ✓ (已完成,见上表 CSRF-Token)
- [x] SQL 注入与预编译 ✓ (已完成,见上表 SQL注入与预编译)
- [x] OAuth 2.0 安全要点 ✓ (已完成,见上表 OAuth2-PKCE与授权码;含 PKCE、mix-up、令牌端点认证)
- [x] SameSite Lax 行为细节 ✓ (已完成,见上表 Cookie安全属性与SameSite)
- [x] HTTPS 与 HSTS ✓ (已完成,见上表 Cookie安全属性与SameSite 的 HSTS 部分)
- [x] 浏览器 Cookie Same-Origin 流程 ✓ (已完成,见上表 Cookie安全属性与SameSite 的存储/检索模型)
- [x] OAuth 2.0 资源服务器侧:DPoP / mTLS 发送方约束 ✓ (已完成,见上表 DPoP与发送方约束令牌)
- [x] CSP 进阶 ✓ (已完成,见上表 CSP strict-dynamic与违规报告 与 TrustedTypes与DOMXSS)
- [ ] 令牌受众校验 `aud`(RFC 9068 JWT 访问令牌 profile)与 JWT 访问令牌的签发/校验
- [ ] Fetch Metadata(Request-Context)作为 CSRF 纵深的完整策略
- [ ] 账户安全:密码重置流程、账户枚举、凭据填充防护
- [x] 跨窗口隔离:COOP/COEP/CORP ✓ (已完成,见上表 COOPCOEP跨源隔离;XS-Leaks 手法清单待补)
- [x] 子资源完整性(SRI)与第三方脚本治理 ✓ (已完成,见上表 SRI与完整性策略)
- [ ] HTTP 请求走私(CL.TE / TE.CL,RFC 9112 §6.3 消息体长度判定分歧)
- [ ] DOM Clobbering 与 `document.write` 注入面
- [ ] 开放重定向(CWE-601)与 URL 校验的分歧
