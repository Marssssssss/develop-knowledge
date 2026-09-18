# 网络安全

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [TLS-1.3握手/](./TLS-1.3握手/) | TLS 1.3 握手协议 (RFC 8446):1-RTT、0-RTT、X25519、HKDF |
| [SYN-Cookie/](./SYN-Cookie/) | SYN Cookie 防 SYN Flood:D.J. Bernstein 1996 序列号编码,Linux 内核 SHA1 |
| [XDP包过滤/](./XDP包过滤/) | eBPF/XDP 高速包过滤(Linux 4.8+):Native/Generic/Offloaded,DDoS 防护 |
| [IKEv2-ESP/](./IKEv2-ESP/) | IKEv2 密钥协商 (RFC 7296):IKE_SA_INIT/IKE_AUTH 两轮 4 消息,ESP/AH 子 SA |
| [DNSSEC链式信任/](./DNSSEC链式信任/) | DNSSEC 信任链 (RFC 4033/4034/4035):DNSKEY/RRSIG/DS,根→TLD→域名 |
| [WireGuard握手/](./WireGuard握手/) | WireGuard (Noise_IKpsk2):1-RTT 4 消息握手、ChaCha20-Poly1305、mac1/mac2 cookie |
| [QUIC包头保护/](./QUIC包头保护/) | QUIC 头保护与包保护 (RFC 9001):首字节 mask、header protection、AEAD nonce=IV⊕pkt_num |
| [RPKI前缀源验证/](./RPKI前缀源验证/) | RPKI 前缀源验证 (RFC 6482/8205):ROA/ASPA + 签名对象链 + 覆盖判定 |
| [WPA3-SAE/](./WPA3-SAE/) | WPA3 SAE (RFC 7664/9381 思路):Dragonfly 提交-确认、hash-to-curve、抗离线字典 |
| [TLS-ECH/](./TLS-ECH/) | TLS 1.3 ECH (RFC 9849 + HPKE RFC 9180):加密 ClientHello、外层 AAD、ech_outer_extensions |

## 已完成 demo

| 路径 | 知识点 | 语言 |
| --- | --- | --- |
| `TLS-1.3握手/` | TLS 1.3 握手协议:ClientHello/ServerHello 内嵌 X25519 key_share → (EC)DHE 共享密钥 → HKDF-Extract/Expand 派生 handshake secret & master secret → client/server handshake_traffic_secret → CertificateVerify 签整段握手哈希防 downgrade,Certificate/F{EncryptedExtensions}/inished 都加密(对比 TLS 1.2 证书明文) | C + Python + Go |
| `SYN-Cookie/` | SYN Cookie 防 SYN Flood:服务器**不分配 TCB**,仅用 SHA1 HMAC(src_ip,src_port,dst_ip,dst_port,timestamp,MSS) 生成 32 位 cookie 嵌入 SYN+ACK 序列号(top 5 bit t mod 32 / mid 3 bit MSS 编码 / bottom 24 bit hash),客户端回 ACK 时校验 cookie 才分配连接 | C + Python + Go |
| `XDP包过滤/` | eBPF/XDP 在网卡驱动 RX 路径最早期 hook,XDP_DROP 丢弃、XDP_PASS 放行、XDP_TX 同口回送、XDP_REDIRECT 重定向到其它 NIC 或 AF_XDP socket;26Mpps/core 单核丢包性能;verifier 静态校验无越界 | C (BPF + libxdp/userspace loader) + Python (userspace 控制器) |
| `IKEv2-ESP/` | IKEv2 协商 IPsec SA:RFC 7296 §1.1.1 IKE_SA_INIT(DH 交换 + nonce)+ IKE_AUTH(身份 + 签名/PSK + 第一个 Child SA)2 轮 4 消息;ESP 隧道/传输模式加密+认证双重保护(AEAD AES-GCM/ChaCha20-Poly1305);支持 NAT-T (RFC 3948 UDP 4500 封装)、MOBIKE (RFC 4555) 移动切换 | C (HMAC-SHA256/SP 伪代码) + Python + Go |
| `DNSSEC链式信任/` | DNSSEC 签名:RRset 用 ZSK 私钥签 → RRSIG;DNSKEY RRset 用 KSK 私钥签 → RRSIG;父域用 DS 摘要(child KSK 哈希)记录 DS RRset → RRSIG;验证器从根 trust anchor(KSK 公钥)走 DNSKEY→[DS→DNSKEY]*→RRset 链;不存在证明用 NSEC/NSEC3 哈希防 zone walking | C (签名/验证伪代码) + Python + Go |
| `WireGuard握手/` | Noise_IKpsk2 握手:SPa1/SPr1 会话密钥 + 发起方 hello → 响应方 hello(1-RTT 即建成),X25519 静态-临时 DH 混合、chacha20poly1305 AEAD、HKDF 两条链 derive_key;mac1 = MAC(HMAC-BLAKE2s, responder_pubkey, msg) 抗扫端口、mac2 在负载压力下返 cookie;索引与重放窗口 | C + Python + Go |
| `QUIC包头保护/` | RFC 9001 两级保护:先 AEAD 加密 payload,再用采样自密文的 5 字节 mask 异或**首字节低 4 位**(长头为低 4 位)与包号字段;长/短包头格式、包号编码长度由首字节低位决定;nonce = IV ⊕ 左填零 pkt_num;**必须先解头保护才能读包号再解包**故解密顺序不可颠倒 | C + Python + Go |
| `RPKI前缀源验证/` | ROA(RFC 6482)用 ASID+前缀+最大长度把「ASN 有权起源该前缀」写成签名对象;ASPA(草案)约束 AS 的提供者集合;ROA 校验用 maxLength 覆盖判定(前缀长度 ≤ maxLength 才算覆盖);验证链是从 IANA 信任锚经 RIR/NIR 到 LIR 的签名对象链(RFC 6488 CMS + RFC 3779 资源证书);无效(Invalid)与「无 ROA」(NotFound)必须分开处理 | C + Python + Go |
| `WPA3-SAE/` | SAE(Dragonfly)提交-确认两轮:双方各自构造 PWE(hash-to-curve,本 demo 用 h=1 计数器循环的简化版,完整版用 SSWU/Elligator2 + isogeny)、以 (PWE·pwd)·(r) 交换并互相验证,确认值 cn = H(scalar, element, peer 标量);私有标量必须由**拒绝采样**从 p−1 的乘法子群取,且双方 r/s 之和须非零;旧式 4 次握手被替换为**抗离线字典**的 PAKE(每次尝试都需在线交互),同时天然提供前向保密;PMK = H(scalar,...,K) 而非直接用 DH 结果 | C + Python + Go |
| `TLS-ECH/` | RFC 9849 加密 ClientHello:HPKE(RFC 9180)Base 模式把 ClientHelloInner 封进外层握手,`ClientHelloOuterAAD` = 外层握手但 ECH 扩展 payload 换成**等长零**(故长度永不随明文变化),密钥/SNI 由 DNS HTTPS 记录里的 ECHConfigList 分发;客户端再送 `ech_outer_extensions`(0xfd00) 压缩重复扩展(4 条 MUST abort:引用缺失/重复/引用 ECH 自己/相对顺序不一致);服务端解密失败必须**继续用手上外层信息走完**(防降级侧信道),成功则在 ServerHello.random 末 8 字节放 accept_confirmation | C + Python + Go |

## 待研究

- [x] TLS 1.3 握手协议 ✓
- [x] SYN Cookie 防 SYN Flood ✓
- [x] XDP 包过滤 ✓
- [x] IKEv2 密钥协商 ✓
- [x] DNSSEC 信任链 ✓
- [x] WireGuard 协议 ✓ (Noise_IKpsk2 1-RTT 握手 + mac1/mac2 cookie)
- [x] QUIC 协议 ✓ (RFC 9001 头保护 + 包保护,两级加密顺序)
- [x] BGP 安全(RPKI/BGPSEC) ✓ (ROA/ASPA + 签名对象链 + maxLength 覆盖判定)
- [x] WPA3 SAE 握手 ✓ (Dragonfly 提交-确认,抗离线字典 PAKE)
- [ ] MACsec (IEEE 802.1AE)
- [x] TLS 1.3 ECH (Encrypted ClientHello) ✓ (RFC 9849 + HPKE RFC 9180)
- [ ] 匿名与混淆传输(obfs4 / Shadowsocks AEAD)
- [ ] DoH/DoT 与加密 DNS 传输(RFC 8484/7858)
- [ ] 网络协议模糊测试与状态机覆盖

## 配套概念

- **OSI 模型**:网络安全覆盖 L2(ARP 防护)/L3(IPsec/RPKI)/L4(SYN Cookie/firewall)/L7(TLS/DNSSEC)
- **零信任**:默认所有流量不信任,逐跳加密+认证
- **纵深防御**:边界防护(WAF/防火墙)+ 传输加密(TLS)+ 应用层签名(JWT)+ 端到端审计(IDS)
- **RFC/IETF 标准**:本文档对应 demo 都基于 RFC 8446(TLS 1.3)/RFC 4987(SYN Cookie)/RFC 7296(IKEv2)/RFC 4033-4035(DNSSEC)/RFC 9001(QUIC-TLS)/RFC 6482 与 8205(RPKI ROA/ASPA)/RFC 9180 与 9849(HPKE/ECH)权威规范
- **「加密的层次」**:链路(WireGuard/MACsec) / 传输(QUIC/TLS) / 名字(ECH/DoH) —— 各层能隐藏的信息量不同,ECH 隐藏的是 SNI 而非目标 IP
