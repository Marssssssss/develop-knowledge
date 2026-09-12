# 密码学

## 子领域

- **对称加密**：AES、ChaCha20、SM4
- **非对称加密**：RSA、ECC（Curve25519）
- **哈希**：SHA-2、SHA-3、SM3、BLAKE3
- **签名**：ECDSA、Ed25519、SM2
- **密钥派生**：PBKDF2、Argon2、HKDF

## 已完成 demo

| 路径 | 知识点 | 语言 |
| --- | --- | --- |
| [对称加密/AES-GCM/](./对称加密/AES-GCM/) | AES-GCM 认证加密(NIST SP 800-38D,GHASH + GCTR + AEAD) | Python (从零) + Go (stdlib crypto/cipher) |
| [非对称加密/RSA-PSS/](./非对称加密/RSA-PSS/) | RSA-PSS 概率签名(RFC 8017 §8.1 + MGF1) | Python (从零) + Go (stdlib crypto/rsa) |
| [签名/Ed25519/](./签名/Ed25519/) | Ed25519 签名(RFC 8032,Curve25519 twisted Edwards) | Python (从零) + Go (stdlib crypto/ed25519) |

## 待研究

- [ ] TLS 1.3 握手流程(密码学套件维度)
- [ ] SM2 / SM3 / SM4 国密合规套件
- [ ] ECDSA 与 secp256k1(区块链场景)
- [ ] X25519 ECDH 密钥交换(与 Ed25519 同源曲线)
- [ ] BLS 签名(聚合签名,适用 PoS)
- [ ] 零知识证明(zk-SNARK/Groth16 入门)