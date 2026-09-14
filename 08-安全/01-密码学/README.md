# 密码学

## 子领域

- **对称加密**:AES-GCM、ChaCha20-Poly1305
- **非对称加密**:RSA-PSS、Curve25519/X25519、ECC
- **哈希**:SHA-256、SHA-3、BLAKE2
- **消息认证**:HMAC-SHA256、CMAC、Poly1305
- **签名**:Ed25519、ECDSA、BLS
- **密钥派生**:HKDF、PBKDF2、Argon2id
- **密码哈希**:Argon2id、bcrypt、scrypt

## 已完成 demo

| 路径 | 知识点 | 语言 |
| --- | --- | --- |
| [对称加密/AES-GCM/](./对称加密/AES-GCM/) | AES-GCM 认证加密(NIST SP 800-38D,GHASH + GCTR + AEAD) | Python (从零) + Go (stdlib) |
| [对称加密/ChaCha20-Poly1305/](./对称加密/ChaCha20-Poly1305/) | ChaCha20-Poly1305 AEAD(RFC 8439,20 轮 QR + Poly1305 2^130-5) | Python (从零) + Go (stdlib) |
| [非对称加密/RSA-PSS/](./非对称加密/RSA-PSS/) | RSA-PSS 概率签名(RFC 8017 §8.1 + MGF1) | Python (从零) + Go (stdlib) |
| [签名/Ed25519/](./签名/Ed25519/) | Ed25519 签名(RFC 8032,Curve25519 twisted Edwards) | Python (从零) + Go (stdlib) |
| [哈希/SHA-256/](./哈希/SHA-256/) | SHA-256 安全哈希(FIPS 180-4 §6.2,64 轮压缩) | C + Python + Go |
| [消息认证/HMAC-SHA256/](./消息认证/HMAC-SHA256/) | HMAC-SHA256(RFC 2104 + RFC 4231,ipad 0x36 + opad 0x5C) | C + Python + Go |
| [密钥交换/X25519/](./密钥交换/X25519/) | X25519 ECDH(RFC 7748,Montgomery ladder + clamping) | C + Python + Go |
| [密码派生/Argon2id/](./密码派生/Argon2id/) | Argon2id 内存硬密码哈希(RFC 9106) | C + Python + Go |

## 待研究

- [ ] TLS 1.3 握手流程(密码学套件维度)
- [ ] SM2 / SM3 / SM4 国密合规套件
- [ ] ECDSA 与 secp256k1(区块链场景)
- [ ] HKDF 密钥派生(RFC 5869)
- [ ] BLS 签名(聚合签名,适用 PoS)
- [ ] 零知识证明(zk-SNARK/Groth16 入门)
- [ ] Post-quantum(CRYSTALS-Kyber/Dilithium)