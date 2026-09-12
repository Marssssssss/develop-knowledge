# IKEv2 密钥协商与 ESP / AH(RFC 7296)

## 简介

**IKEv2**(Internet Key Exchange Version 2,RFC 7296,2014)是 IPsec 体系的**控制平面** — 用来协商 IPsec **安全关联 (SA)**:加密算法、密钥、生命周期、模式。它通过 2 个 exchange 共 **4 个消息** 完成 IKE SA 与第一个 ESP/AH Child SA 的建立,**比 IKEv1 更简洁**(IKEv1 要 6+ 消息,极易被攻击)。

- **关键问题**:IPsec 需要双方共享会话密钥 + 加密算法 + 模式(隧道/传输)等 SA 参数,直接手工配置 (RFC 4306 原始目标就是替代人工 keying) 无法 scale;且 SA 必须定期 rekey(典型 1 小时/100 GB)。
- **关键概念**:
  - **IKE_SA**:**控制面通道**,IKEv2 协议自身用的安全关联,生命周期长(数小时-天)
  - **Child SA**:IPsec 数据面 SA,用 ESP 或 AH 保护用户数据;IKEv2 一次能创建多个 Child SA
  - **IKE_SA_INIT**(第 1 个 exchange):DH 密钥交换 + 协商加密参数 + 互发 Nonce
  - **IKE_AUTH**(第 2 个 exchange):身份认证 + 创建第一个 Child SA(所有消息用 IKE_SA_INIT 派生的密钥加密)
  - **后续 exchange**:`CREATE_CHILD_SA`(新建/重协商 SA)、`INFORMATIONAL`(保活、删除、错误报告)
  - **ESP**(Encapsulating Security Payload,RFC 4303):**加密 + 认证**(AEAD,如 AES-GCM)的 IP 包格式,实际部署中绝大多数只用 ESP
  - **AH**(Authentication Header,RFC 4302):只认证不加密,几乎没人用
- **历史**:IKEv1 由 RFC 2407 (DOI) / 2408 (ISAKMP) / 2409 三个文档定义,1998 年发布。IKEv2 RFC 4306 (2005) 由 Kaufman 等重写。RFC 5996 (2010) 修订。RFC 7296 (2014) 把 5996 升级为 Internet Standard。2024 年 RFC 9370 引入多个 Key Exchange 抗量子。

## 原理详解

### IKEv2 4 消息握手 + ESP 数据流

```
Initiator                                Responder
  |--- IKE_SA_INIT(req) ------------------>|
  |   HDR(SPI_i, SPI_r=0, MsgID=0, Flags=...)
  |     SAi1: 提议 [AES-GCM-128, AES-CBC-128, ChaCha20-Poly1305]
  |                + [ECDH-X25519, ECDH-P-256] + [PRF-HMAC-SHA256]
  |     KEi:  X25519 公钥 (32 B)
  |     Ni:   Nonce_i (32 B)
  |     NAT-T 支持: NAT_DETECTION_SOURCE_IP + hashes
  |
  |--- 计算 SKEYSEED = prf(Ni | Nr, g^ir) ---
  |
  |<-- IKE_SA_INIT(resp) ------------------|
  |   HDR(SPI_i, SPI_r, MsgID=0)
  |     SAr1: 选择 AES-GCM-128 + ECDH-X25519 + PRF-HMAC-SHA256
  |     KEr:  X25519 公钥
  |     Nr:   Nonce_r
  |     <可选 NAT-D> 检测是否经过 NAT
  |
  |--- IKE_AUTH(req) 加密 + 完整性 ---->|
  |   HDR(...) (用 SK_ei 加密,SK_ai 完整性保护)
  |     IDi:   身份 = ID_FQDN = "vpn.example.com" / ID_IPV4 = 192.0.2.1
  |     CERT:  X.509 证书 (RFC 4945 Detached Cert Chain 可选)
  |     AUTH:  prf(prf(PSK/SK_p, "Key Pad for IKEv2"), <消息 octets>)
  |            — 对整个到本次消息为止的 IKE 消息的签名(类似 TLS 1.3 CertificateVerify)
  |     SAi2:  Child SA 提议 (ESP/AES-GCM-128 + PFS group)
  |     TSi/TSr: 流量选择器(子网)
  |
  |<-- IKE_AUTH(resp) 加密 + 完整性 ----|
  |     IDr:   身份
  |     CERT:  服务器证书
  |     AUTH:  签名
  |     SAr2:  选定 AES-GCM-128 + SHA-256
  |     TSi/TSr: 协商流量选择器
  |
  |--- 双方立刻可以用 Child SA (ESP) 通信 ---|
  |
  |--- [Child SA: ESP 隧道包] -- X-Routes ---|
  |   新 IPv4 头 (网关)
  |   ESP 头 (SPI 32 bit | SeqNo)
  |   IV (12 B) + AES-GCM 密文
  |   ESP Trailer + ICV (Integrity Check Value, 通常 12-16 B tag)
```

### 关键密码学步骤(RFC 7296 §2)

#### 1. IKE_SA_INIT 派生密钥

```text
SK_undefined = 未初始化
g^ir        = X25519(i_priv, r_pub) = X25519(r_priv, i_pub)      # ECDHE 共享秘密

SKEYSEED    = prf(Ni | Nr, g^ir)                                  # prf = PRF-HMAC-SHA256
[7] SK_ai   = prf(SKEYSEED, Ni | Nr | 0x01)                       # Initiator IKE 完整性
[7] SK_ar   = prf(SKEYSEED, Ni | Nr | 0x02)                       # Responder IKE 完整性
[7] SK_ei   = prf(SKEYSEED, Ni | Nr | 0x03)                       # Initiator IKE 加密
[7] SK_er   = prf(SKEYSEED, Ni | Nr | 0x04)                       # Responder IKE 加密
[5.5] SK_pi = prf(SKEYSEED, Ni | Nr | 0x05)                       # Initiator AUTH key
[5.5] SK_pr = prf(SKEYSEED, Ni | Nr | 0x06)                       # Responder AUTH key
```

#### 2. AUTH 值计算(身份认证)

```text
证书签名(PSK 用 prf):
  AUTH = prf(prf(Shared Secret, "Key Pad"), <IDi 消息 octets>)
  (RFC 7296 §2.15)

证书签名(ECDSA/RSA):
  AUTH = 签名私钥, 内容 = "IKE SA Signature" + IKE_SA_INIT HDR + Nonce_i + <SA + KE + Nr 包含的消息>
```

#### 3. Child SA 密钥

```text
KEYMAT = prf(SK_d, Ni | Nr)                                      # SK_d 由 IKE_AUTH 阶段从 SKEYSEED 派生

其中 SK_d   = prf(SKEYSEED, Ni | Nr | g^ir | 0x00)               # "derived" key

参数取够 KEYMAT 字节:
  SK_ei_child = KEYMAT[0..(enc_key_len)]                          # Initiator ESP 加密密钥 (AES-128: 16 B)
  SK_ai_child = KEYMAT[(enc_key_len)..(enc_key_len+auth_key_len)] # Initiator ESP 完整性密钥
  ... (同样分 Responder 版本)
```

### ESP 包结构(RFC 4303)

```
              ┌─────────────────────────────────────────┐
IPv4 包:      │  原 IP 头 │ 原 payload │                 │
              └─────────────────────────────────────────┘

ESP 隧道模式:
              ┌────────┬────────┬─────────┬─────────┬───────┬───────┬───────┐
ESP 包:       │新 IP头 │ ESP HDR │ ESP 密文 │ ESP Trl │ Pad │Pad Len│ Next │ ICV │
              │(20+B) │ (8 B)   │ (变长)   │ (0+)    │(0+)│ (1B) │ HDR │(12+  │
              │        │ SPI+Seq │(IV+CT)   │         │     │       │(1B) │16 B)│
              └────────┴────────┴─────────┴─────────┴───────┴───────┴───────┘
                                            └─ 加密范围 ─┘
                        └─ 认证范围 (从 ESP HDR 到 ESP Trl+Pad) ──────────┘
```

- **SPI** (Security Parameter Index,32 bit):唯一标识 Child SA
- **SeqNo** (32 bit 或 64 bit with ESN):抗重放,接收端滑动窗口检测 (RFC 6479 default 32 packet window)
- **IV** (12 B for AES-GCM):让 nonce 重合率小于 2⁻³²
- **ICV** (12-32 B):AEAD tag (AES-GCM 16 B,ChaCha20-Poly1305 16 B)
- **Anti-replay**:接收方维护 `[SeqNo - 32, SeqNo]` 滑动窗口,窗口外 + 老于窗口左界的包被丢

### NAT-T(RFC 3948)

ESP 是 IP protocol 50,很多家用路由器/NAT 不会转发非 TCP/UDP 协议 → IPsec 在 NAT 环境下会断流。IKEv2 在 IKE_SA_INIT 期间会发 NAT_DETECTION_SOURCE_IP/RESPONDER_IP 探测,如果任一端发现收到的 UDP 500 包源端口/地址对不上 → 启用 NAT-T:把 ESP 整个封装在 UDP 4500 里面,然后正常 ESP 数据流转。

### MOBIKE(RFC 4555)

移动设备在 Wi-Fi/cellular 之间切换时 IP 会变,IPsec SA 会断。MOBIKE 扩展让 IKEv2 在保持 SA 的前提下,用 INFORMATIONAL 消息更新地址 + 流量。

### 与 TLS 1.3 的同源对比

| 概念 | TLS 1.3 | IKEv2 / ESP |
| --- | --- | --- |
| 密码套件 | cipher_suites (5 个 AEAD) | Encryption/Integrity/PRF/DH transform (很多组合) |
| Key 派生 | HKDF-Extract → HKDF-Expand-Label | prf(Ni \| Nr, g^ir) → SKEYSEED → SK_ai/ar/ei/er |
| CertVerify 防降级 | ✓ | ✗ (IKEv2 用 SPI 标识 + 强收到 push back) |
| Forward secrecy | ✓ (ECDHE only) | ✓(可选 PFS in Child SA via CREATE_CHILD_SA 携带新 KE) |
| Rekey | NewSessionTicket + PSK | CREATE_CHILD_SA exchange(显式重协商) |
| NAT-Traversal | 自带(N/A) | RFC 3948 (UDP 4500) |
| Mobile | 自带 | MOBIKE (RFC 4555) |

## 环境准备

- 操作系统:任意。
- 语言版本:
  - Python 3.7+(hashlib hmac + prf)
  - Go 1.20+
  - C + OpenSSL `libcrypto`(HMAC、X25519、AES-GCM)
- 依赖:C 编译需 `libssl-dev`。
- **无需 root**:本 demo 纯算法演示。

## 运行方式

```bash
# C
gcc -O2 -Wall -Wextra c/ikev2_demo.c -lcrypto -o ikev2
./ikev2

# Python
python3 python/ikev2_demo.py

# Go
cd go && go run ikev2_demo.go
```

输出:打印:
1. 双方 DH 共享密钥(应相等)
2. SKEYSEED 派生值 + SK_ai / SK_ar / SK_ei / SK_er (RFC 7296 §2.14)
3. SK_d → KEYMAT 派生 Child SA 的 ESP 加密 key + ESP 完整性 key
4. 模拟构造一个 ESP 隧道包并用 SPI + AES-GCM 加密 → 输出解密还原

## 关键代码片段

完整实现见 `python/ikev2_demo.py`。`prf+` 复用(RFC 7296 §2.13):

```python
def prfplus(key, seed, count):
    """RFC 7296 §2.13 prf+ = prf 链式扩展"""
    T = b''
    for i in range(count):
        T += prf(key, T[-16:] + seed + bytes([i + 1]))
    return T

def derive_keys(shared_secret, ni, nr):
    skeyseed = prf(ni + nr, shared_secret)
    sk_d  = prf_plus(skeyseed, ni + nr + shared_secret_bytes + b'\x00', 1)  # 取 32 B
    sk_ai = prf_plus(skeyseed, ni + nr + b'\x01', 1)
    sk_ar = prf_plus(skeyseed, ni + nr + b'\x02', 1)
    sk_ei = prf_plus(skeyseed, ni + nr + b'\x03', 1)
    sk_er = prf_plus(skeyseed, ni + nr + b'\x04', 1)
    sk_pi = prf_plus(skeyseed, ni + nr + b'\x05', 1)
    sk_pr = prf_plus(skeyseed, ni + nr + b'\x06', 1)
    return sk_d, sk_ai, sk_ar, sk_ei, sk_er, sk_pi, sk_pr

def derive_child_sa_keys(sk_d, ni, nr, key_len, integ_len):
    keymat = prf_plus(sk_d, ni + nr, ceil((2 * key_len + 2 * integ_len) / prf_output_len))
    sk_ei_child = keymat[:key_len]
    sk_ai_child = keymat[key_len:key_len + integ_len]
    sk_er_child = keymat[key_len + integ_len:2 * key_len + integ_len]
    sk_ar_child = keymat[2 * key_len + integ_len:2 * key_len + 2 * integ_len]
    return sk_ei_child, sk_ai_child, sk_er_child, sk_ar_child
```

## 性能与边界

- **X25519 DH**:~70 µs (OpenSSL 1.1.1+)
- **PRF (HMAC-SHA256)**:~250 ns / 32 B 输出
- **AES-128-GCM**:~10 GB/s throughput (AES-NI)
- **一次完整 IKEv2 4 消息握手**:客户端+服务端合计 < 1 ms (本地)
- **理论 SA 数量**:单个 IPsec 网关可同时维持几十万条 Child SA(routing table 用 trie 查找 SPI)
- **支持的 cipher**(常见):
  - Encryption: AES-CBC-128/192/256 / AES-GCM-16-128/256 / ChaCha20-Poly1305
  - Integrity: HMAC-SHA2-256-128 / HMAC-SHA2-384-192 / HMAC-SHA2-512-256
  - PRF: HMAC-SHA2-256 / HMAC-SHA2-384 / HMAC-SHA2-512 / AES-CMAC-128
  - DH group: 14/15/16/17/18/19/20/21/22/23/24/25/26/27/28/29/30 (X25519 = group 31)
- **重协商 PFS**:`CREATE_CHILD_SA` 携带 KE 载荷=新 DH(重新 X25519+nonce) → 不依赖老 KEYMAT 有完美前向保密
- **最大 Child SA 寿命**:RFC 推荐 ≤ 8 小时 / 100 GB

## 注意事项与常见坑

- **选错 PRF/Encryption/DH group 组合**:IKEv2 不像 TLS 那样有 cipher_suites 联合编号,必须保证 Initiator 和 Responder 的 transform 列表某子集交集非空。常见失败:一方只放 AES-CBC,另一方只放 AES-GCM → 失败。
- **AUTH 算法**:CERT 用了 Ed25519 签名,AUTH 必须算 `SIG(ed25519, "IKE SA Signature" + IKE_SA_INIT_MsgID_padded)`。RSA/ECDSA 同样套路。
- **Idempotency**:IKE 消息必须有 MsgID(递增);接收方也要滑动窗口(默认 32 消息)防重放。
- **重协商 SPI**:IKE_SA_INIT 后 IKE SA 用 SPI pair (SPI_i, SPI_r) 唯一标识,**重协商是新建一对 SPI**;OLD IKE SA + NEW IKE SA 并存。
- **NAT-T MD5fuzz**:NAT_DETECTION_SOURCE_IP/RESPONDER_IP 载荷计算 `HASH(IKE peer IP, peer port)`,任一端发现收到的源 IP/port hash ≠ 自己计算的 → 强制 NAT-T(UDP 4500)。
- **MOBIKE 用 INFORMATIONAL**:每次地址变化 update,代价是网络 ID 变化导致 Path detection 抖动。
- **AES-CBC + 完整性密钥单独**:CBC ESP 用 key1 加密,key2 做 HMAC(独立);AES-GCM 只用 1 个 key + 16 B ICV=tag 自身;不能混用密钥的尺寸。
- **`tcpdump` 看 ESP 看不到**:ESP 默认密文,没有端口号特征;要识别 → 在 tcpdump 用 `proto 50` 抓。
- **PSK vs 证书认证**:PSK 模式 AUTH = prf(PSK, "Key Pad for IKEv2"),不能 PSK 短(< 64 bit);RFC 漏洞认证需配 EAP。
- **`linux/net/xfrm.h`** 用户态 API:**不要在 production 绕过 Strongswan/WireGuard** 等成熟实现,自己写很易踩坑(SA lifetime/route/MTU)。

## 对比 / 选型

| 方案 | 适用 | 优劣 |
| --- | --- | --- |
| **IKEv2 + ESP** | 站点到站点 VPN / 远程访问 / 企业 | 标准、最强、生态成熟(NSS/strongSwan/LibreSwan/Racoon2) |
| **WireGuard** | 个人 / 中小企业 / 云主机 | 极简单(单 4KB 代码),但不支持 NAT-T 之外的特性(IKEv2 MOBIKE,RFC 7383 抗量子) |
| **OpenVPN** | 跨 NAT、客户端软件 | UDP/TCP 任意、好过 NAT;但不是 IPsec 标准 |
| **SSL/TLS VPN**(OpenVPN) | 通过 Web proxy 的环境 | 隧道走 443,易穿防火墙;非 IPsec |
| **MACsec (802.1AE)** | LAN 层 2 加密 | 与 IPsec 互补,但只能在二层 |
| **HTTPS/SSH port-forward** | 单端口代理 | 简单,不适合全流量 |

**WireGuard 已经在很多场景取代 IKEv2**,但 IKEv2 仍是**站点到站点 VPN / 企业 / 运营商基础设施**的首选,有 OEM 路由器支持。

## 参考资料(实际阅读过的权威来源)

- [RFC 7296 — Internet Key Exchange Protocol Version 2 (IKEv2)](https://www.ietf.org/rfc/inline-errata/rfc7296.html) — IKEv2 主规范,§2 Key Derivation / §1.1.1 Usage Scenarios / §3.1 创建 Child SA / §2.13 prf+ / §2.14 密钥派生算式
- [RFC 4303 — IP Encapsulating Security Payload (ESP)](https://www.rfc-editor.org/rfc/rfc4303.html) — ESP 包格式、加密/认证范围、Anti-replay 窗口、ESN 扩展
- [RFC 3948 — UDP Encapsulation of IPsec ESP Packets](https://www.rfc-editor.org/rfc/rfc3948.html) — NAT-T 协议核心,IPsec ESP 透过 NAT 走 UDP 4500
- [RFC 4555 — IKEv2 Mobility and Multihoming Protocol (MOBIKE)](https://www.rfc-editor.org/rfc/rfc4555.html) — 移动切换不用重协商 SA,只需 INFORMATIONAL + 地址更新
- [RFC 8247 — Algorithm Implementation Requirements and Usage Guidance for IKEv2](https://www.rfc-editor.org/rfc/rfc8247.html) — MUST/SHOULD/MAY 实现推荐,弃用 RSA encryption 等
- [NetBSD IPsec FAQ](https://netbsd.hu/docs/network/ipsec) — ESP / AH / IPComp 对比 + IKEv1 vs IKEv2 演化(IKEv1 → Phase 1 Main/Aggressive → Phase 2 Quick Mode;IKEv2 → IKE_SA_INIT + IKE_AUTH + CREATE_CHILD_SA)
- [dev.to — IKEv2 and IPsec: The VPN Already Built Into Your Phone](https://dev.to/havenmessenger/ikev2-and-ipsec-the-vpn-already-built-into-your-phone-5mo) — AH vs ESP、身份保护、为什么要 NAT-T、MOBIKE 在手机上的实际体现
- [RFC 9370 — Multiple Key Exchanges in IKEv2](https://www.rfc-editor.org/rfc/rfc9370.html) — 抗量子(PQC) 多 KE 扩展,可与 X25519 组合(KEM 类算法)
- [Strongswan — IKEv2 Configuration](https://docs.strongswan.org/docs/strongswanDocumentation.html) — 实际工程大量参考文档
