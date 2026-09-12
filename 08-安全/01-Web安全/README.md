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

## 已完成 demo(08-安全/01-Web安全 首批)

| 路径 | 知识点 | 语言 |
| --- | --- | --- |
| `XSS与CSP/` | XSS 三类型(Reflected/Stored/DOM-based)+ 5 大上下文编码 + CSP nonce 机制 + WAF 黑名单反模式 | Python + JavaScript |
| `CSRF-Token/` | CSRF Token 同步器令牌 + Signed Double Submit Cookie + SameSite + Fetch Metadata + Origin/Referer 兜底 | Python + JavaScript |
| `SQL注入与预编译/` | SQL 注入与参数化查询(sqlite3 prepared statements + 跨语言占位符对照) | Python + Go |
| `JWT验证/` | JWT HS256 验证(RFC 7519 §A.1 测试向量 + RFC 8725 Algorithm Confusion 防御 + jti 黑名单) | Python + Go |
| `Same-Origin与CORS/` | 同源策略(RFC 6454 三元组)+ CORS(Simple Request + Preflight + 凭据请求) | Python + JavaScript |

## 待研究

- [x] XSS 攻击与 CSP 防御 ✓ (已完成,见上表 XSS与CSP)
- [x] CSRF Token 机制 ✓ (已完成,见上表 CSRF-Token)
- [x] SQL 注入与预编译 ✓ (已完成,见上表 SQL注入与预编译)
- [ ] OAuth 2.0 安全要点
- [ ] SameSite Lax 行为细节
- [ ] HTTPS 与 HSTS
- [ ] 浏览器 Cookie Same-Origin 流程
