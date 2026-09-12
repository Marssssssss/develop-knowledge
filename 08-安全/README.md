# 08 安全

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-密码学/](./01-密码学/) | 对称/非对称/哈希/签名 |

> 目录命名有重叠以方便按主题跳转，正式安全研究请参考 [OWASP Top 10](https://owasp.org/www-project-top-ten/)。

## 已完成 demo

| 路径 | 知识点 |
| --- | --- |
| `01-密码学/对称加密/AES-GCM/` | AES-GCM 认证加密(NIST SP 800-38D,GHASH + GCTR + AEAD) |
| `01-密码学/非对称加密/RSA-PSS/` | RSA-PSS 概率签名(RFC 8017 §8.1 + MGF1) |
| `01-密码学/签名/Ed25519/` | Ed25519 签名(RFC 8032,Curve25519 twisted Edwards) |

## 待研究

- [ ] AES / RSA / Ed25519 实现原理 ✓ (08-安全/01-密码学 首批已完成)
- [ ] TLS 1.3 握手
- [ ] XSS 与 CSP
- [ ] OAuth 2.0 流程