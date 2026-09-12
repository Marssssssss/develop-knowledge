# TLS 1.3 握手协议 (RFC 8446)

## 简介

**TLS 1.3**(RFC 8446,2018)是 TLS 协议的重大重写,目标:**减少握手延迟**、**移除所有已知脆弱的密码套件**、**强制前向保密 (Forward Secrecy)**。

- **关键问题**:TLS 1.2 完整握手需要 2 个 RTT 才能发应用数据;RSA 密钥交换没有前向保密,泄露服务端私钥 = 解密所有历史会话;CBC/SHA-1/MD5 等老算法有 BEAST、POODLE、Logjam 等历史攻击;证书明文传输让被动观察者能识别网站。
- **关键概念**:
  - **1-RTT 握手**:客户端把 X25519 公钥塞进 ClientHello,服务端立刻能算出 ECDHE 共享秘密
  - **0-RTT 回连**:用之前会话的 PSK 在第一个包就带应用数据(⚠️ 易被重放攻击,只能用于幂等请求)
  - **HKDF**(RFC 5869):(EC)DHE 共享密钥 → Extract → Expand 多层派生 handshake secret、master secret、traffic key
  - **加密证书**:ServerHello 之后所有消息 (EncryptedExtensions/Certificate/CertificateVerify/Finished) 都用 handshake_traffic_secret 加密 → 被动观察者看不到证书
  - **CertificateVerify**:对整个握手 transcript 签名 → 防 downgrade attack
- **历史**:TLS 1.0 (1999 RFC 2246) → TLS 1.1 (2006 RFC 4346) → TLS 1.2 (2008 RFC 5246) → **TLS 1.3 (2018 RFC 8446)**。TLS 1.0/1.1 已在 2021 年被 IETF 弃用。

## 原理详解

### TLS 1.3 vs TLS 1.2 对比

| 特性 | TLS 1.2 | TLS 1.3 |
| --- | --- | --- |
| 握手 RTT(新建) | 2 RTT | **1 RTT** |
| 握手 RTT(恢复) | 1 RTT | **0 RTT** |
| 密钥交换 | RSA / DHE / ECDHE / PSK | **ECDHE / DHE only**(RSA 移除) |
| 前向保密 | 可选 | **强制** |
| 数字证书加密 | ❌ 明文 | ✅ 加密 |
| 密码套件数 | 37+ | **5** (全 AEAD) |
| 算法白名单 | RC4/3DES/CBC 可选 | **全部移除**,只允许 AEAD |
| 抗降级攻击 | 部分 | **强**(CertificateVerify) |

### TLS 1.3 1-RTT 握手时序

```
Client                                          Server
  |--- ClientHello ---------------------------->|
  |   - client_random (32 B)
  |   - cipher_suites (TLS_AES_256_GCM_SHA384, ...)
  |   - supported_groups: [X25519, P-256]
  |   - key_share: {group: X25519, key_exchange: <32 B 公钥>}
  |   - signature_algorithms: [ecdsa_secp256r1_sha256, ...]
  |   - server_name (SNI)
  |
  |                          derive handshake_secret = HKDF-Extract(salt, ECDHE)
  |                          derive server_handshake_traffic_secret
  |                          derive server_write_key + server_write_iv
  |
  |<-- ServerHello ------------------------------|
  |   - server_random (32 B)
  |   - cipher_suite: TLS_AES_256_GCM_SHA384
  |   - key_share: {group: X25519, key_exchange: <32 B 服务端 X25519 公钥>}
  |
  |<-- {EncryptedExtensions} -------------------|   ← 用 server_handshake_traffic_secret 加 AEAD
  |<-- {Certificate} (含证书链)-------------------|
  |<-- {CertificateVerify}-----------------------|     sig over full transcript,防 downgrade
  |<-- {Finished} (HMAC transcript)-------------|
  |
  |   验证 server Finished、Certificate、CertificateVerify
  |   派生 client_handshake_traffic_secret + client_application_traffic_secret
  |
  |--- {Finished} ------------------------------>|
  |--- [Application Data] -------------------->|   ← 第一字节应用数据带 Finished 发,1 RTT 完成
  |
  |<-- [Application Data] ---------------------|
```

### 关键步骤

1. **ClientHello**:客户端生成临时 ECDHE 密钥对(`x25519_private`, `x25519_public`),把公钥塞进 `key_share` 扩展。
2. **ServerHello**:服务端选 cipher suite 和 ECDHE group,生成自己的临时密钥对,把公钥也返回。
3. **密钥派生**(RFC 8446 §7.1):
   ```
   ECDHE_shared_secret = X25519(client_priv, server_pub)
   PSK  (在 0-RTT,可选)
   early_secret = HKDF-Extract(salt=0, key=PSK)               # PSK=0 时无 0-RTT
   derived  = Derive-Secret(early_secret, "c e traffic", ClientHello..server_Finished)
   handshake_secret = HKDF-Extract(salt=derived, key=ECDHE_shared_secret)
   derived  = Derive-Secret(handshake_secret, "s hs traffic", ...)
   server_handshake_traffic_secret = HKDF-Expand-Label(handshake_secret, "s hs traffic", transcript_hash, 32)
   server_write_key = HKDF-Expand-Label(server_handshake_traffic_secret, "key", "", 16)
   server_write_iv  = HKDF-Expand-Label(server_handshake_traffic_secret, "iv", "", 12)
   ```
4. **EncryptedExtensions/Certificate/CertificateVerify/Finished**:这些消息在服务端用 server_handshake_traffic_secret 派生的 key+iv + AEAD (AES-128-GCM) 加密。
5. **Finished**:对到目前为止**所有握手消息的 transcript hash** 做 HMAC(Finished 自身除外),作为整段握手的"认证",任何中间人篡改都会破坏。
6. **master_secret**:服务端 Finished 后再派生 `master_secret → client/server_application_traffic_secret_N`(0-RTT 模式下也派生 `resumption_master_secret`)。

### 0-RTT 模式

```
Client 第一次连接成功:
  Server -> NewSessionTicket { lifetime, ticket_age_add, PSK_name, binder_key }

Client 第二次(0-RTT):
  ClientHello
    + early_data: "[GET /index.html HTTP/1.1\r\nHost: example.com\r\n...]"  (用 early_traffic_secret 加 AEAD)
    + pre_shared_key: { PSK_name = ticket, obfuscated_ticket_age }
    + binder: HMAC(binder_key, ClientHello..binder)

Server:
  解 PSK → early_secret → client_early_traffic_secret → 解密 early_data(必须为幂等请求!)
  正常 1-RTT 流程...
  EndOfEarlyData + Finished
```

⚠️ 0-RTT 数据**没有 replay 保护**:攻击者捕获合法 0-RTT ClientHello 可以无限重放。TLS 1.3 明确警告只能用于 GET/HTTP 缓存等幂等请求。

### 移除的不安全特性

| 移除项 | 原因 |
| --- | --- |
| RSA 密钥交换 | 静态 RSA 无前向保密,私钥泄露 = 全历史会话可解密 |
| 静态 DH 密钥 | 同上 |
| DHE_EXPORT | FREAK/Logjam 攻击 (降级到 512 位) |
| CBC 密码套件 | BEAST / Lucky13 / POODLE padding oracle |
| RC4 | 流密码统计偏差可恢复密文 |
| MD5 / SHA-1 签名 | 碰撞攻击 (SHAttered 2017) |
| 压缩 | CRIME(压缩比 = 信息泄漏) |
| 重协商 | CVE-2009-3555 重协商注入 |
| Session ID 恢复 | 服务端有状态,改为 Session Ticket(PSK) |

## 环境准备

- 操作系统:任意(Linux / macOS / Windows 都能跑,纯算法/字节流,不依赖 OS 协议栈)
- 语言版本:
  - Python 3.7+(用了 `secrets` 模块、`hashlib` 标准化 API)
  - Go 1.20+
  - C 编译器 (`gcc`/`clang`) + OpenSSL 开发库(`libssl-dev` / `brew install openssl`)

```bash
# Debian/Ubuntu
sudo apt-get install libssl-dev

# macOS
brew install openssl
```

## 运行方式

```bash
# Python
python3 python/tls13_handshake.py

# Go
cd go && go run tls13_handshake.go

# C (需要 libssl 提供 X25519)
gcc -O2 -Wall -Wextra -pedantic c/tls13_handshake.c -lcrypto -o tls13
./tls13
```

输出:打印客户端临时密钥对、服务端临时密钥对、ECDHE 共享秘密(双方应当相等)、HKDF 派生的 `server_handshake_traffic_secret` / `server_write_key` / `server_write_iv`,以及用 `server_write_key` 加密 EncryptedExtensions/Certificate/Finished 的 ciphertext + AEAD tag(并由客户端再用同样密钥解密验证通过)。

## 关键代码片段

完整实现见 `python/tls13_handshake.py`,核心步骤对应原理详解中的编号:

```python
# 1. 客户端生成临时 X25519 密钥对 → 把公钥塞 ClientHello
client_priv = x25519_random_scalar()           # 32 B 随机
client_pub  = x25519_basepoint_mult(client_priv)

client_hello = {
    'random':        os.urandom(32),
    'cipher_suites': [TLS_AES_128_GCM_SHA256],
    'supported_groups': [X25519],
    'signature_algs':  [ECDSA_SECP256R1_SHA256],
    'key_share':       {'group': X25519, 'pub': client_pub},
}

# 2. 服务端选 cipher + ECDHE group,生成自己临时密钥对
server_priv = x25519_random_scalar()
server_pub  = x25519_basepoint_mult(server_priv)
server_hello = {
    'cipher_suite': TLS_AES_128_GCM_SHA256,
    'key_share':    {'group': X25519, 'pub': server_pub},
}

# 3. ECDHE 共享秘密 (X25519) ← 双方都必须算出相同值
shared_client = x25519_scalar_mult(client_priv, server_pub)
shared_server = x25519_scalar_mult(server_priv, client_pub)
assert shared_client == shared_server

# 4. HKDF 派生(RFC 5869)→ handshake_secret
handshake_secret = hkdf_extract(salt=b'\x00'*32, ikm=shared_client)

# 5. Derive-Secret (transcript 为 ClientHello..server_Finished 之前的哈希)
transcript_hash = sha256(client_hello_bytes + server_hello_bytes)
server_hs_traffic_secret = derive_secret(handshake_secret, "s hs traffic", transcript_hash)
client_hs_traffic_secret = derive_secret(handshake_secret, "c hs traffic", transcript_hash)

# 6. AEAD key + iv
server_write_key = hkdf_expand_label(server_hs_traffic_secret, "key", b"", 16)
server_write_iv  = hkdf_expand_label(server_hs_traffic_secret, "iv",  b"", 12)

# 7. 服务端用 server_write_key + iv 加密 Certificate/Finished/...
nonce = xor(server_write_iv, seq_num.to_bytes(12, 'big'))
aead  = AES.new(server_write_key, AES.MODE_GCM, nonce=nonce)
ciphertext, tag = aead.encrypt_and_digest(certificate_msg)

# 8. 客户端验证:同样派生 client_hs_traffic_secret 再解密
# (对称密钥由 client 端写 key + iv 派生,server 端写 key + iv 派生于 server)
```

> 本 demo 演示 "ECDHE 共享密钥派生 + HKDF 派生 traffic key + AEAD 加解密" 全流程;不做真实 TCP 传输、不做证书解析(那部分会引入 8000+ 行 ASN.1 + X.509 解析,不是本 demo 的核心)。

## 性能与边界

- **X25519 一次 ECDHE ≈ 70 µs**(OpenSSL 现代 CPU),1 RTT 延迟主要由网络决定,握手 CPU 占比小
- **HKDF**:HKDF-Extract + 单次 HKDF-Expand-Label < 10 µs(32 字节输出)
- **AEAD 加解密**:AES-128-GCM 大包吞吐 ~10 GB/s(Intel AES-NI),握手阶段几十字节消息可忽略
- **支持的 Cipher suites**(5 个,MUST 实现):
  - `TLS_AES_128_GCM_SHA256`
  - `TLS_AES_256_GCM_SHA384`
  - `TLS_CHACHA20_POLY1305_SHA256`
  - `TLS_AES_128_CCM_SHA256`
  - `TLS_AES_128_CCM_8_SHA256`
- **支持的群组**:`x25519`、`secp256r1`、`secp384r1`、`secp521r1`、`x448`、`ffdhe2048`、`ffdhe3072`、`ffdhe4096`、`ffdhe6144`、`ffdhe8192`
- **签名算法**:`ecdsa_secp256r1_sha256`、`rsa_pss_rsae_sha256`、`ed25519`、`rsa_pkcs1_sha256` 等

## 注意事项与常见坑

- **TLS 1.2 兼容降级**(兼容性回退):很多 server 仍跑 TLS 1.2,需要在 ClientHello 加 `supported_versions` 扩展 + `signature_algorithms` extension;否则回到 TLS 1.2 慢路径。
- **0-RTT replay**:0-RTT 数据无 server-side replay 防护,**只能**用于 GET、缓存查询等幂等请求;POST/PUT/付款请求必须 1-RTT 或全握手。
- **ServerHello 之后一切加密**:实现 TLS 1.3 状态机时很容易在切换 cipher 前发应用数据 → 客户端拒绝。**严格按 RFC 8446 §1.3 图 2 状态机**。
- **CertificateVerify 算法**:必须**与证书公钥类型对应**(Ed25519 证书用 `ed25519`,RSA 证书用 `rsa_pss_rsae_sha256`)→ RFC 8446 §4.2.3。
- **transcript hash**:Finished = HMAC(transcript_hash),transcript 必须包含**从 ClientHello 到 Finished 之前所有消息**(包括未加密握手消息的明文)。CertificateVerify 是对同样的 transcript 的**签名**。
- **HKDF-Expand-Label 与 RFC 5869 不同**:TLS 1.3 在 `HKDF-Expand-Label(secret, label, context, length)` 用 `HKDF-Expand(prk, info, length)`,`info = struct.pack(">H", len(label)) + label + struct.pack(">H", 0) + context`。
- **OpenSSL X25519 API 兼容**:`EVP_PKEY_X25519` 在 OpenSSL 1.1.1+ 才有,老版本只能凑 ECDH P-256。

## 对比 / 选型

| 实现 | 语言 | 性能 | 适用场景 |
| --- | --- | --- | --- |
| OpenSSL | C | 最高 | 服务端/客户端库主流 |
| BoringSSL | C | 最高 | Google 维护,Cronet/QUIC |
| Rustls | Rust | 高 | 嵌入式/Cloudflare |
| Go crypto/tls | Go | 高 | 服务端 |
| Bouncy Castle | Java | 中 | Android/通用 |
| BearSSL | C | 高 | 嵌入式/TLS 1.2 + 1.3 极简实现 |

## 参考资料(实际阅读过的权威来源)

- [RFC 8446 — The Transport Layer Security (TLS) Protocol Version 1.3](https://www.rfc-editor.org/rfc/rfc8446.html) — TLS 1.3 主规范,握手状态机、HKDF、Cipher Suites、0-RTT 全部细节
- [RFC 5869 — HMAC-based Extract-and-Expand Key Derivation Function (HKDF)](https://www.rfc-editor.org/rfc/rfc5869.html) — HKDF-Extract/HKDF-Expand 完整算法
- [RFC 7748 — Elliptic Curves for Security (X25519/X448)](https://www.rfc-editor.org/rfc/rfc7748.html) — X25519 Montgomery ladder scalar multiplication 严格定义(Montgomery ladder 防侧信道)
- [TLS 1.3: Faster, Simpler, More Secure — ProtocolCodes Guide](https://protocolcodes.com/guides/tls-13-protocol-guide/) — TLS 1.2 vs 1.3 RTT 对比图、关键移除特性清单
- [TLS 1.3 Explained: The Handshake That Protects the Internet — StealthCloud](https://stealthcloud.ai/cryptography/tls-1-3-explained) — HKDF 派生阶梯图、CertificateVerify/防 downgrade 说明
- [TLS/SSL Deep Dive: Handshake, Certificates, and CAs — KinDa Technical](https://kindatechnical.com/networking/tls-ssl-deep-dive-handshake-certificates-cas.html) — TLS 1.3 ClientHello 各扩展字段说明,SNI 用法
- [How SSL/TLS Works: A Developer's Guide — Cyber Security in Plain English](https://cyber-security-in-plain-english.com/post/developers/networking/how-tls-ssl-works-for-developers/) — TLS 1.2 vs 1.3 完整特性对比表,1-RTT/0-RTT 时序图
- [OpenSSL Wiki — TLS 1.3](https://wiki.openssl.org/index.php/TLSv1.3) — OpenSSL 启用 TLS 1.3 客户端/服务端示例
