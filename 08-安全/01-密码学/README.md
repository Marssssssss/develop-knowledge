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
| [TLS1.3密钥调度/](./TLS1.3密钥调度/) | **417** TLS 1.3 密钥调度(RFC 8446 §7.1,HKDF-Expand-Label + 五层秘密链 + 报文转录绑定 + 每记录 nonce = 序号 XOR 静态 IV + KeyUpdate) | C + Python + Go |
| [国密SM2SM3SM4/](./国密SM2SM3SM4/) | **418** 国密三件套(GM/T 0004 SM3 XOR 前馈 + GM/T 0002 SM4 非平衡 Feistel 含 L/L′ 两种线性层 + GM/T 0003 SM2 含 Z_A 与 k 复用泄漏) | Python + Go |
| [BLS聚合签名/](./BLS聚合签名/) | **419** BLS 聚合签名(draft-irtf-cfrg-bls-signature-05,Miller 循环 + 扭映射 + 最终幂 + 恶意公钥攻击与三道防线) | Go + Python |
| [后量子MLKEM/](./后量子MLKEM/) | **420** ML-KEM 后量子 KEM(FIPS 203,不完全 NTT + Montgomery 因子 + basemul + CBD 采样 + 有损压缩 + FO 隐式拒绝) | Python |
| [Groth16零知识证明/](./Groth16零知识证明/) | **421** Groth16 zk-SNARK(R1CS→QAP 整除判定 + 可信设置 + 3 群元素证明 + 3 配对验证 + 有毒废料伪造) | Python + Go |

## 待研究

- [x] TLS 1.3 握手流程(密码学套件维度) ✓ (2026-09-19,417)
- [x] SM2 / SM3 / SM4 国密合规套件 ✓ (2026-09-19,418)
- [x] ECDSA(P-256,含 RFC 6979 确定性 k) ✓ (2026-09-16,secp256k1 区块链变体待后续)
- [x] HKDF 密钥派生(RFC 5869) ✓ (2026-09-16)
- [x] BLS 签名(聚合签名,适用 PoS) ✓ (2026-09-19,419)
- [x] 零知识证明(zk-SNARK/Groth16 入门) ✓ (2026-09-19,421)
- [x] Post-quantum(CRYSTALS-Kyber/Dilithium) ✓ (2026-09-19,420,ML-KEM;ML-DSA 待后续)
- [ ] ML-DSA / CRYSTALS-Dilithium 后量子签名(FIPS 204)
- [ ] 门限签名与分布式密钥生成(Shamir + FROST)
- [ ] 可搜索加密与同态加密(BFV/CKKS 入门)
- [ ] 证书链与 PKI(X.509 解析 + 吊销 OCSP/CRL)