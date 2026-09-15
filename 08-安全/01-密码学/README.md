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
| [哈希/SHA-3-Keccak/](./哈希/SHA-3-Keccak/) | SHA-3/Keccak-f[1600] 海绵函数(FIPS 202,θ/ρ/π/χ/ι + pad10*1 + SHAKE XOF) | C + Python + Go |
| [密钥派生/HKDF/](./密钥派生/HKDF/) | HKDF Extract-and-Expand 密钥派生(RFC 5869,TLS 1.3 秘密树底座) | C + Python + Go |
| [签名/ECDSA-P256/](./签名/ECDSA-P256/) | ECDSA + RFC 6979 确定性签名(P-256 仿射点运算 + HMAC-DRBG 消随机数) | C + Python + Go |
| [消息认证/HOTP-TOTP/](./消息认证/HOTP-TOTP/) | HOTP/TOTP 一次性口令(RFC 4226/6238,动态截断 + 30s 时间步) | C + Python + Go |
| [密码派生/PBKDF2/](./密码派生/PBKDF2/) | PBKDF2 口令密钥派生(RFC 8018 §5.2 + RFC 6070 向量,含 NUL 陷阱) | C + Python + Go |

## 待研究

- [ ] TLS 1.3 握手流程(密码学套件维度)
- [ ] SM2 / SM3 / SM4 国密合规套件
- [x] ECDSA(P-256,含 RFC 6979 确定性 k) ✓ (2026-09-16,secp256k1 区块链变体待后续)
- [x] HKDF 密钥派生(RFC 5869) ✓ (2026-09-16)
- [ ] BLS 签名(聚合签名,适用 PoS)
- [ ] 零知识证明(zk-SNARK/Groth16 入门)
- [ ] Post-quantum(CRYSTALS-Kyber/Dilithium)