# 08 安全

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-密码学/](./01-密码学/) | 对称/非对称/哈希/签名 |
| [01-Web安全/](./01-Web安全/) | 请求侧攻防(XSS / CSRF / 注入 / JWT / CORS) |
| [02-网络安全/](./02-网络安全/) | 链路侧(传输安全、协议指纹) |
| [03-应用安全与供应链/](./03-应用安全与供应链/) | 代码与制品侧(SAST / 依赖漏洞 / SBOM / 供应链完整性 / CI 加固) — 🗺 已登记,demo 待做 |

> 目录命名有重叠以方便按主题跳转，正式安全研究请参考 [OWASP Top 10](https://owasp.org/www-project-top-ten/)。
> 三个子领域的分工按「攻击者能控制什么输入」划分:请求参数 / 网络包 / **依赖版本与构建环境**。

## 已完成 demo

| 路径 | 知识点 |
| --- | --- |
| `01-密码学/对称加密/AES-GCM/` | AES-GCM 认证加密(NIST SP 800-38D,GHASH + GCTR + AEAD) |
| `01-密码学/非对称加密/RSA-PSS/` | RSA-PSS 概率签名(RFC 8017 §8.1 + MGF1) |
| `01-密码学/签名/Ed25519/` | Ed25519 签名(RFC 8032,Curve25519 twisted Edwards) |
| `01-Web安全/XSS与CSP/` | XSS 三类型 + 5 大上下文编码 + CSP nonce 机制 |
| `01-Web安全/CSRF-Token/` | CSRF Token 同步器令牌(Signed Double Submit + SameSite + Fetch Metadata) |
| `01-Web安全/SQL注入与预编译/` | SQL 注入与参数化查询(sqlite3 prepared statements) |
| `01-Web安全/JWT验证/` | JWT HS256 验证(RFC 7519 §A.1 示例向量 + RFC 8725 Algorithm Confusion 防御) |
| `01-Web安全/Same-Origin与CORS/` | 同源策略 + CORS(Simple Request + Preflight + 凭据请求) |
| `01-密码学/哈希/SHA-3-Keccak/` | SHA-3/Keccak-f[1600] 海绵函数(FIPS 202,五步轮函数 + SHAKE XOF) |
| `01-密码学/密钥派生/HKDF/` | HKDF Extract-and-Expand 密钥派生(RFC 5869,TLS 1.3 秘密树底座) |
| `01-密码学/签名/ECDSA-P256/` | ECDSA + RFC 6979 确定性签名(P-256 + HMAC-DRBG 消随机数) |
| `01-密码学/消息认证/HOTP-TOTP/` | HOTP/TOTP 一次性口令(RFC 4226/6238,动态截断 + 30s 时间步) |
| `01-密码学/密码派生/PBKDF2/` | PBKDF2 口令密钥派生(RFC 8018 + RFC 6070 向量,含 NUL 陷阱) |

## 待研究

- [x] AES / RSA / Ed25519 实现原理 ✓ (08-安全/01-密码学 首批已完成)
- [x] TLS 1.3 握手
- [x] XSS 与 CSP
- [x] OAuth 2.0 流程