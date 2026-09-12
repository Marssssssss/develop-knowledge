# 网络安全

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [TLS-1.3握手/](./TLS-1.3握手/) | TLS 1.3 握手协议 (RFC 8446):1-RTT、0-RTT、X25519、HKDF |
| [SYN-Cookie/](./SYN-Cookie/) | SYN Cookie 防 SYN Flood:D.J. Bernstein 1996 序列号编码,Linux 内核 SHA1 |
| [XDP包过滤/](./XDP包过滤/) | eBPF/XDP 高速包过滤(Linux 4.8+):Native/Generic/Offloaded,DDoS 防护 |
| [IKEv2-ESP/](./IKEv2-ESP/) | IKEv2 密钥协商 (RFC 7296):IKE_SA_INIT/IKE_AUTH 两轮 4 消息,ESP/AH 子 SA |
| [DNSSEC链式信任/](./DNSSEC链式信任/) | DNSSEC 信任链 (RFC 4033/4034/4035):DNSKEY/RRSIG/DS,根→TLD→域名 |

## 已完成 demo

| 路径 | 知识点 | 语言 |
| --- | --- | --- |
| `TLS-1.3握手/` | TLS 1.3 握手协议:ClientHello/ServerHello 内嵌 X25519 key_share → (EC)DHE 共享密钥 → HKDF-Extract/Expand 派生 handshake secret & master secret → client/server handshake_traffic_secret → CertificateVerify 签整段握手哈希防 downgrade,Certificate/F{EncryptedExtensions}/inished 都加密(对比 TLS 1.2 证书明文) | C + Python + Go |
| `SYN-Cookie/` | SYN Cookie 防 SYN Flood:服务器**不分配 TCB**,仅用 SHA1 HMAC(src_ip,src_port,dst_ip,dst_port,timestamp,MSS) 生成 32 位 cookie 嵌入 SYN+ACK 序列号(top 5 bit t mod 32 / mid 3 bit MSS 编码 / bottom 24 bit hash),客户端回 ACK 时校验 cookie 才分配连接 | C + Python + Go |
| `XDP包过滤/` | eBPF/XDP 在网卡驱动 RX 路径最早期 hook,XDP_DROP 丢弃、XDP_PASS 放行、XDP_TX 同口回送、XDP_REDIRECT 重定向到其它 NIC 或 AF_XDP socket;26Mpps/core 单核丢包性能;verifier 静态校验无越界 | C (BPF + libxdp/userspace loader) + Python (userspace 控制器) |
| `IKEv2-ESP/` | IKEv2 协商 IPsec SA:RFC 7296 §1.1.1 IKE_SA_INIT(DH 交换 + nonce)+ IKE_AUTH(身份 + 签名/PSK + 第一个 Child SA)2 轮 4 消息;ESP 隧道/传输模式加密+认证双重保护(AEAD AES-GCM/ChaCha20-Poly1305);支持 NAT-T (RFC 3948 UDP 4500 封装)、MOBIKE (RFC 4555) 移动切换 | C (HMAC-SHA256/SP 伪代码) + Python + Go |
| `DNSSEC链式信任/` | DNSSEC 签名:RRset 用 ZSK 私钥签 → RRSIG;DNSKEY RRset 用 KSK 私钥签 → RRSIG;父域用 DS 摘要(child KSK 哈希)记录 DS RRset → RRSIG;验证器从根 trust anchor(KSK 公钥)走 DNSKEY→[DS→DNSKEY]*→RRset 链;不存在证明用 NSEC/NSEC3 哈希防 zone walking | C (签名/验证伪代码) + Python + Go |

## 待研究

- [x] TLS 1.3 握手协议 ✓
- [x] SYN Cookie 防 SYN Flood ✓
- [x] XDP 包过滤 ✓
- [x] IKEv2 密钥协商 ✓
- [x] DNSSEC 信任链 ✓
- [ ] WireGuard 协议
- [ ] QUIC 协议
- [ ] BGP 安全(RPKI/BGPSEC)
- [ ] WPA3 SAE 握手
- [ ] MACsec (IEEE 802.1AE)
- [ ] TLS 1.3 ECH (Encrypted ClientHello, RFC 9578)

## 配套概念

- **OSI 模型**:网络安全覆盖 L2(ARP 防护)/L3(IPsec/RPKI)/L4(SYN Cookie/firewall)/L7(TLS/DNSSEC)
- **零信任**:默认所有流量不信任,逐跳加密+认证
- **纵深防御**:边界防护(WAF/防火墙)+ 传输加密(TLS)+ 应用层签名(JWT)+ 端到端审计(IDS)
- **RFC/IETF 标准**:本文档对应 demo 都基于 RFC 8446(TLS 1.3)/RFC 4987(SYN Cookie)/RFC 7296(IKEv2)/RFC 4033-4035(DNSSEC)权威规范
