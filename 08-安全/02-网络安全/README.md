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
| [TCP盲注入与RFC5961/](./TCP盲注入与RFC5961/) | TCP 盲重置/盲注入与 RFC 5961 缓解:精确匹配 RCV.NXT、challenge ACK、SACK 边缘与两级限速 |
| [DNS缓存投毒与RFC5452/](./DNS缓存投毒与RFC5452/) | DNS 伪造应答的组合难度 (RFC 5452):P_s 与 P_cs 公式、生日攻击、端口随机化增益 |
| [证书透明度Merkle审计/](./证书透明度Merkle审计/) | CT Merkle 审计 (RFC 9162):MTH 定义、包含性证明、一致性证明的 append-only 验证 |
| [SSH传输层与密钥交换/](./SSH传输层与密钥交换/) | SSH 传输层 (RFC 4253 + RFC 8308):二进制包填充对齐、KEX 三条件协商、密钥派生链 |
| [IPv6隐私地址与SLAAC/](./IPv6隐私地址与SLAAC/) | IPv6 临时地址 (RFC 4941 + RFC 8981):MD5 链与带密钥 PRF 两代 IID 生成、生命周期约束 |

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
| `TCP盲注入与RFC5961/` | RFC 5961 + Linux `tcp_input.c` 实读:RST 拆成三分支(精确匹配 RCV.NXT 才 reset、窗内非精确发 challenge ACK、窗外静默丢弃),而 SYN 是**不管序列号在哪都发 challenge**;内核额外放宽 `rcv_nxt-1`(仅 CLOSE_WAIT/LAST_ACK/CLOSING)与最右 SACK 块边缘;ACK 判据由 `SND.UNA-(2^31-1)` 收紧到 `SND.UNA-MAX.SND.WND`,可接受取值从 2^31 降到 65537;challenge ACK 限速**每秒复位成 `[half, limit+half-1]` 的随机值**,故单秒上限是 limit+half-1 而非 limit;难度从 2^31/窗口 抬回 2^31 | Python(68 断言实跑) + Go(人工审查 + 三项静态检查) |
| `DNS缓存投毒与RFC5452/` | RFC 5452 §7.2 组合难度实测:`P_s = D·F/(N·P·I)`、`P_cs = 1-(1-P_s)^(T/TTL)`,代入默认值化简分母恰为 **1638400**;TTL=3600 与 R=7000 pps 下 24h/7d 得 9.77%/51.3%、TTL=60 下 24min/3h/9h 得 9.77%/53.7%/90.1%,与文档表述逐条吻合;发现三处文档口径不一致并**记录不改正**:§8.1 的「7 秒 / 116 小时」用的是 N=1 而非 §7 默认的 2.5、§7 的「4 Mbit/s 达 50%」是线性近似(精确公式要 5.89 Mbit/s)、§8 的「285 Gb/s」是从 4 Mbit/s 乘 64000 得来而非 416 Mbit/s;**降 TTL 反而加速被投毒**,因为 P_cs 只依赖 T/TTL | Python(44 断言实跑) + Go(人工审查 + 三项静态检查) |
| `证书透明度Merkle审计/` | RFC 9162 §2.1 四组算法逐行实现并用 §2.1.5 的 7 叶树做向量:MTH 用 `0x00`/`0x01` 做域分隔、`k` 是**严格小于 n** 的最大 2 的幂;四个包含性证明 `[b,h,l]`/`[c,g,l]`/`[f,j,k]`/`[i,k]` 与三个一致性证明 `[c,d,g,l]`/`[l]`/`[i,j,k]` 全部与文档逐字节一致;验证端靠 `fn`/`sn` 双游标,`fn == sn` 专门处理非满树最右路径;一致性验证时 `first` 为 2 的幂要先补 `first_hash`,这正是 `PROOF(4,D7)=[l]` 只有一个节点却能过的缘由;n=0..20 全组合共 675 断言 | Python(675 断言实跑) + Go(人工审查 + 三项静态检查) |
| `SSH传输层与密钥交换/` | RFC 4253 §6/§7/§8 + RFC 8308:包填充必须按 **max(cipher block, 8)** 对齐(流密码也不例外)、padding∈[4,255]、整包≥16;**KEX 与 cipher 的选法不同** —— KEX 多一层「主机密钥是否具备所需能力」检查,故 `rsa2048-sha256`(需加密型密钥)在 ed25519 下被跳过而在 ssh-rsa 下被选中;六把密钥由 `HASH(K 加 H 加 字母 加 session_id)` 派生且**扩展是追加式**(64 字节的前 32 == 32 字节版);`session_id` 终身不变而密钥随每次 KEX 的 H 变;RFC 8308 的 `ext-info-c`/`ext-info-s` 名字故意不同以保证永不匹配成 KEX 方法 | Python(401 断言实跑) + Go(人工审查 + 三项静态检查) |
| `IPv6隐私地址与SLAAC/` | RFC 4941 与 RFC 8981 两代临时地址算法:RFC 4941 取 MD5 的**左** 64 位并清 U/L 位、冲突时把 history 换成右 64 位重来;RFC 8981 用带密钥 PRF 且**从最低有效位取**,不再动 U/L 位(RFC 7136:IID 无特殊位),冲突靠 `DAD_Counter` 加 1;生命周期里**只有 preferred 减 DESYNC_FACTOR、valid 不减**,且「是否创建」用的是严格大于 REGEN_ADVANCE(默认 5 秒),恰好等于时不创建;`Preferred Lifetime = 0` 的 RA 让地址 deprecated 但**不得**触发新建 | Python(65 断言实跑) + Go(人工审查 + 三项静态检查) |

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
- [x] TLS 1.3 ECH (Encrypted ClientHello) ✓ (RFC 9849 + HPKE RFC 9180)
- [x] TCP 盲注入与 RFC 5961 缓解 ✓ (精确匹配 RCV.NXT + challenge ACK + 两级限速)
- [x] DNS 缓存投毒与 RFC 5452 ✓ (组合难度公式 + 生日攻击 + 端口随机化)
- [x] 证书透明度 Merkle 审计 ✓ (RFC 9162 包含性证明与一致性证明)
- [x] SSH 传输层与密钥交换 ✓ (RFC 4253 包协议/协商/派生 + RFC 8308 扩展协商)
- [x] IPv6 隐私地址与 SLAAC ✓ (RFC 4941 MD5 链 + RFC 8981 带密钥 PRF)
- [ ] MACsec (IEEE 802.1AE)
- [ ] 匿名与混淆传输(obfs4 / Shadowsocks AEAD)
- [ ] DoH/DoT 与加密 DNS 传输(RFC 8484/7858)
- [ ] 网络协议模糊测试与状态机覆盖
- [ ] BGPsec 路径签名(RFC 8205,与已完成的 RPKI 起源验证互补)
- [ ] 隧道与 VPN 的流量指纹(TLS-in-TLS / WireGuard 握手特征)

## 配套概念

- **OSI 模型**:网络安全覆盖 L2(ARP 防护)/L3(IPsec/RPKI)/L4(SYN Cookie/firewall)/L7(TLS/DNSSEC)
- **零信任**:默认所有流量不信任,逐跳加密+认证
- **纵深防御**:边界防护(WAF/防火墙)+ 传输加密(TLS)+ 应用层签名(JWT)+ 端到端审计(IDS)
- **RFC/IETF 标准**:本文档对应 demo 都基于 RFC 8446(TLS 1.3)/RFC 4987(SYN Cookie)/RFC 7296(IKEv2)/RFC 4033-4035(DNSSEC)/RFC 9001(QUIC-TLS)/RFC 6482 与 8205(RPKI ROA/ASPA)/RFC 9180 与 9849(HPKE/ECH)权威规范
- **「加密的层次」**:链路(WireGuard/MACsec) / 传输(QUIC/TLS) / 名字(ECH/DoH) —— 各层能隐藏的信息量不同,ECH 隐藏的是 SNI 而非目标 IP
