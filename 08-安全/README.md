# 08 安全

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-密码学/](./01-密码学/) | 对称/非对称/哈希/签名 |
| [01-Web安全/](./01-Web安全/) | 请求侧攻防(XSS / CSRF / 注入 / JWT / CORS) |
| [02-网络安全/](./02-网络安全/) | 链路侧(传输安全、协议指纹) |
| [03-应用安全与供应链/](./03-应用安全与供应链/) | 代码与制品侧(SAST / 依赖漏洞 / SBOM / 供应链完整性 / CI 加固) — ✅ 两批共 10 个 demo |

> 目录命名有重叠以方便按主题跳转，正式安全研究请参考 [OWASP Top 10](https://owasp.org/www-project-top-ten/)。
> 三个子领域的分工按「攻击者能控制什么输入」划分:请求参数 / 网络包 / **依赖版本与构建环境**。

## 已完成 demo

| 路径 | 知识点 |
| --- | --- |
| `01-密码学/对称加密/AES-GCM/` | AES-GCM 认证加密(NIST SP 800-38D,GHASH + GCTR + AEAD) |
| `01-密码学/非对称加密/RSA-PSS/` | RSA-PSS 概率签名(RFC 8017 §8.1 + MGF1) |
| `01-密码学/签名/Ed25519/` | Ed25519 签名(RFC 8032,Curve25519 twisted Edwards) |
| `01-Web安全/XSS与CSP/` | XSS 三类型 + 5 大上下文编码 + CSP nonce 机制 |
| `01-Web安全/CSRF-Token/` | CSRF Token 同步器令牌(Signed Double Submit + SameSite + Fetch Metadata) |
| `01-Web安全/SQL注入与预编译/` | SQL 注入与参数化查询(sqlite3 prepared statements) |
| `01-Web安全/JWT验证/` | JWT HS256 验证(RFC 7519 §A.1 示例向量 + RFC 8725 Algorithm Confusion 防御) |
| `01-Web安全/Same-Origin与CORS/` | 同源策略 + CORS(Simple Request + Preflight + 凭据请求) |
| `01-密码学/哈希/SHA-3-Keccak/` | SHA-3/Keccak-f[1600] 海绵函数(FIPS 202,五步轮函数 + SHAKE XOF) |
| `01-密码学/密钥派生/HKDF/` | HKDF Extract-and-Expand 密钥派生(RFC 5869,TLS 1.3 秘密树底座) |
| `01-密码学/签名/ECDSA-P256/` | ECDSA + RFC 6979 确定性签名(P-256 + HMAC-DRBG 消随机数) |
| `01-密码学/消息认证/HOTP-TOTP/` | HOTP/TOTP 一次性口令(RFC 4226/6238,动态截断 + 30s 时间步) |
| `01-密码学/密码派生/PBKDF2/` | PBKDF2 口令密钥派生(RFC 8018 + RFC 6070 向量,含 NUL 陷阱) |
| `01-Web安全/OAuth2-PKCE与授权码/` | 授权码流程 + PKCE(RFC 6749/7636/9700,含 mix-up 与降级防御) |
| `01-Web安全/Cookie安全属性与SameSite/` | Cookie 存储模型 + SameSite + HSTS(RFC 6265/6265bis/6797) |
| `01-Web安全/SSRF与URL解析歧义/` | SSRF 防护 + WHATWG IPv4 解析与解析器分歧 |
| `01-Web安全/访问控制与IDOR/` | 访问控制与对象级授权(RBAC/ABAC/ReBAC + CWE-639) |
| `01-Web安全/点击劫持与框架嵌入/` | X-Frame-Options + CSP frame-ancestors(HTML §7.7) |
| `02-网络安全/TLS-1.3握手/` | TLS 1.3 握手(RFC 8446):1-RTT/0-RTT、X25519 key_share、HKDF 秘密树 |
| `02-网络安全/SYN-Cookie/` | SYN Cookie 防 SYN Flood:无状态序列号编码(HMAC-SHA1 + MSS + 时间戳) |
| `02-网络安全/XDP包过滤/` | eBPF/XDP 早检丢弃:4 种 action、Native/Generic/Offloaded 三模式 |
| `02-网络安全/IKEv2-ESP/` | IKEv2 密钥协商(RFC 7296):IKE_SA_INIT/IKE_AUTH 两轮 + ESP 子 SA |
| `02-网络安全/DNSSEC链式信任/` | DNSSEC 信任链(RFC 4033-4035):DNSKEY/RRSIG/DS + NSEC 防遍历 |
| `02-网络安全/WireGuard握手/` | WireGuard Noise_IKpsk2:1-RTT 握手 + mac1/mac2 cookie 抗反射 |
| `02-网络安全/QUIC包头保护/` | QUIC 头保护与包保护(RFC 9001):首字节 mask + nonce=IV⊕pkt_num |
| `02-网络安全/RPKI前缀源验证/` | RPKI 前缀源验证(RFC 6482/8205):ROA/ASPA + maxLength 覆盖判定 |
| `02-网络安全/WPA3-SAE/` | WPA3 SAE(Dragonfly):提交-确认两轮 PAKE,抗离线字典 |
| `02-网络安全/TLS-ECH/` | TLS 1.3 ECH(RFC 9849 + HPKE RFC 9180):加密 ClientHello + 外层 AAD |
| `03-应用安全与供应链/SAST与污点分析/` | 污点分析三档精度阶梯:flow-insensitive/flow-sensitive/path-sensitive 的误报集与漏报集 |
| `03-应用安全与供应链/依赖漏洞与SBOM/` | SemVer 区间求值 + 传递解析 + 调用图可达性剪枝 + SPDX 2.3 关系(DESCRIBES/CONTAINS/DEPENDS_ON) |
| `03-应用安全与供应链/供应链完整性/` | SLSA v1.0 provenance 验证:subject 摘要绑定、builder.id 定级别、signer-builder 配对、扩展字段忽略 |
| `03-应用安全与供应链/密钥管理/` | CWE-798 硬编码凭据检测:熵上限受字符集约束 → 归一化 + 结构规则 + 上下文白名单 |
| `03-应用安全与供应链/CI流水线加固/` | CI 四类攻击面:表达式注入、action SHA 固定、特权触发器共享缓存投毒、OIDC 短期令牌 |
| `03-应用安全与供应链/CVSSv4评分与宏向量/` | CVSS v4.0:270 条查找表 + EQ1–EQ6 宏向量等价类 + 严重性距离插值 + 无影响短路 |
| `03-应用安全与供应链/TUF更新框架/` | TUF 四角色阈值(按唯一 KEYID 计数)与回滚/冻结/混搭三类攻击的防线 + 可终止委托 |
| `03-应用安全与供应链/in-toto布局与链路验证/` | in-toto link/layout:7 条工件规则按防火墙式顺序消费 + 隐式 `ALLOW *` + 子布局虚拟 link |
| `03-应用安全与供应链/可复现构建/` | `SOURCE_DATE_EPOCH` 是上界、ZIP 1980 下界、`-ffile-prefix-map` 消路径,umask 无法归一化 |
| `03-应用安全与供应链/覆盖率引导模糊测试/` | AFL 边覆盖位图 `cur ^ prev` + 8 个 2 的幂分桶 + 贪心集合覆盖裁剪(99%/95%/75% 跳过) |
| `01-密码学/TLS1.3密钥调度/` | TLS 1.3 密钥调度(RFC 8446 §7.1):HKDF-Expand-Label + 五层秘密链 + 转录绑定 + KeyUpdate |
| `01-密码学/国密SM2SM3SM4/` | 国密三件套:SM3(XOR 前馈)、SM4(非平衡 Feistel + L/L′ 双线性层)、SM2(Z_A + k 复用泄漏) |
| `01-密码学/BLS聚合签名/` | BLS 聚合签名(draft-irtf-cfrg-bls-signature-05):Miller 循环配对 + 恶意公钥攻击三道防线 |
| `01-密码学/后量子MLKEM/` | ML-KEM(FIPS 203):module-LWE + 不完全 NTT + CBD 采样 + 有损压缩 + FO 隐式拒绝 |
| `01-密码学/Groth16零知识证明/` | Groth16 zk-SNARK:R1CS→QAP 整除判定 + 可信设置 + 3 群元素证明 + 3 配对验证 |
| `01-Web安全/DPoP与发送方约束令牌/` | DPoP(RFC 9449 §4.3 十二校验 + jkt 绑定 + 两套独立 nonce)与 RFC 8705 `x5t#S256` 证书绑定 |
| `01-Web安全/TrustedTypes与DOMXSS/` | W3C Trusted Types:§3.4 汇点四分支、default policy 在 report-only 下返回原值、§3.8 属性表 |
| `01-Web安全/CSP strict-dynamic与违规报告/` | CSP3 `'strict-dynamic'` 的信任传递、parser-inserted 分水岭、sample 截断 40 字符、Strict CSP 判据 |
| `01-Web安全/SRI与完整性策略/` | SRI 标准 base64 摘要 +「只验最强那批」+ §3.8 Integrity-Policy 的强制/report-only 判定 |
| `01-Web安全/COOPCOEP跨源隔离/` | HTML §7.1.3 COOP 五种取值与 BCG 切换 + COEP §2.3 fail-open 表 + §3.2.1 CORP check |
| `02-网络安全/TCP盲注入与RFC5961/` | TCP 盲重置/盲注入与 RFC 5961 缓解(RFC 5961 + Linux tcp_input.c 实读) |
| `02-网络安全/DNS缓存投毒与RFC5452/` | DNS 伪造应答的组合难度与端口随机化增益(RFC 5452) |
| `02-网络安全/证书透明度Merkle审计/` | CT Merkle 审计:包含性证明与 append-only 一致性证明(RFC 9162) |
| `02-网络安全/SSH传输层与密钥交换/` | SSH 二进制包协议 + KEX 协商 + 密钥派生(RFC 4253 + RFC 8308) |
| `02-网络安全/IPv6隐私地址与SLAAC/` | IPv6 临时地址两代生成算法与生命周期(RFC 4941 + RFC 8981) |
| `01-密码学/MLDSA后量子签名/` | **636** ML-DSA 后量子签名(FIPS 204):模数 `Q=8380417` 的取整 + hint 提示位 |
| `01-密码学/FROST门限签名/` | **637** FROST 两轮门限签名(RFC 9591):绑定因子 + 可识别中止 |
| `01-密码学/HPKE混合公钥加密/` | **638** HPKE(RFC 9180):DHKEM + 双 `suite_id` + 序号 XOR 出的 nonce |
| `01-密码学/AES密钥包装/` | **639** AES 密钥包装:RFC 3394 的 6n 步 Feistel 与 RFC 5649 的 `A65959A6 ‖ MLI` |
| `01-密码学/SPAKE2口令认证密钥交换/` | **640** SPAKE2 PAKE(RFC 9382):转录 TT 的 8 字节小端长度 + 确认密钥 KcA/KcB |
| `01-密码学/Shamir秘密共享与阈值恢复/` | **641** Shamir 秘密共享三套对照:素数域 / Vault GF(2^8) / SLIP-0039 |
| `01-密码学/JOSE令牌JWS与JWE/` | **642** JOSE JWS/JWE(RFC 7515/7516/7518):AAD 规则 + A128KW + A128CBC-HS256 |
| `01-密码学/恒定时间与侧信道防护/` | **643** 常量时间原语与侧信道:OpenSSL 掩码原语 + BearSSL 执行模型 + `value_barrier` |
| `01-密码学/证书透明化Merkle审计/` | **644** CT Merkle 审计与 SCT/STH 编码(RFC 6962) |
| `01-Web安全/HTTP请求走私CL.TE与TE.CL/` | **703** RFC 9112 §6.3 消息体长度八条判定的优先级 + §7.1.3 chunked 解码 + CL.TE 与 TE.CL 两跳走私 |
| `01-Web安全/FetchMetadata与资源隔离策略/` | **704** W3C Fetch Metadata §2.3 Sec-Fetch-Site 判定算法 + §4.1 重定向链记住最坏一环 + §4.2 Sec- 前缀不可伪造 |
| `01-Web安全/DOMClobbering与命名属性访问/` | **705** HTML §7.2.2.3 supported property names 三部分并集 + property set 两段循环 + 取值优先级 |
| `01-Web安全/账户安全与凭据填充防护/` | **706** NIST SP 800-63B 长度门槛与四个 SHALL NOT + 码点计数与 NFC + 黑名单整串比对 + 连续失败上限 100 |
| `01-Web安全/JWT访问令牌profile与受众校验/` | **707** RFC 9068 typ 必须是 at+jwt + 七个 REQUIRED 声明 + §4 六步校验 + §5 跨 JWT 混淆 |

## 待研究

- [x] AES / RSA / Ed25519 实现原理 ✓ (08-安全/01-密码学 首批已完成)
- [x] TLS 1.3 握手 ✓ (02-网络安全/TLS-1.3握手)
- [x] XSS 与 CSP ✓
- [x] OAuth 2.0 流程 ✓
- [x] 传输层协议安全整批 ✓ (02-网络安全 两批共 10 个:SYN Cookie / XDP / IKEv2 / DNSSEC / WireGuard / QUIC / RPKI / SAE / ECH)
- [x] 密码学深水区整批 ✓ (01-密码学 第 14-18 个:TLS1.3 密钥调度 / 国密 SM2-SM3-SM4 / BLS 聚合签名 / 后量子 ML-KEM / Groth16)
- [x] 网络安全第三批:传输与命名基础设施 ✓ (02-网络安全 第三批 5 个:TCP 盲注入与 RFC 5961 / DNS 缓存投毒与 RFC 5452 / 证书透明度 Merkle 审计 / SSH 传输层 / IPv6 隐私地址)
- [x] 密码学第四/五批:后量子与协议侧 ✓ (01-密码学 636-644 共 9 个:ML-DSA / FROST / HPKE / AES-KW / SPAKE2 / Shamir / JOSE / 恒定时间 / CT Merkle)
- [ ] 后量子迁移与混合模式的**工程**侧(协商与回滚;算法本身见 636/420)
- [ ] MACsec (IEEE 802.1AE)
- [x] 供应链与制品安全 ✓ (03-应用安全与供应链 首批 5 个:污点分析精度阶梯 / 依赖可达性+SPDX / SLSA provenance 验证 / CWE-798 熵判据 / CI 加固)
- [x] 供应链第二批:度量与验证机制 ✓ (03-应用安全与供应链 第二批 5 个:CVSS v4.0 宏向量 / TUF 四角色 / in-toto link 验证 / 可复现构建 / AFL 覆盖率引导)
- [x] Web 安全第三批:浏览器侧纵深防御 ✓ (01-Web安全 第三批 5 个:DPoP 发送方约束 / Trusted Types / CSP strict-dynamic 与报告 / SRI 与 Integrity-Policy / COOP-COEP-CORP 跨源隔离)
- [x] Web 安全第四批:协议分帧分歧 · 请求上下文 · 账户与令牌 ✓ (01-Web安全 第四批 5 个,ID 703-707:HTTP 请求走私 CL.TE/TE.CL / Fetch Metadata 与资源隔离 / DOM Clobbering / 账户安全与凭据填充 / JWT 访问令牌 profile 与 aud 校验)
- [ ] 密钥管理与 HSM/KMS 抽象
- [ ] 后量子迁移与混合模式(X25519MLKEM768 混合密钥交换;ML-KEM 单算法已见 420,ML-DSA 待补)

## 历史欠账(行数超限,待后续轮次拆分)

> 仅记录不修,按「源文件 ≤300 行 / README ≤200 行」体检得出;本轮(2026-09-19 20:00)新产 5 个 demo **均已达标**。

| 文件 | 行数 | 超限 |
| --- | --- | --- |
| `02-网络安全/DNSSEC链式信任/README.md` | 299 | README >200 |
| `02-网络安全/DNSSEC链式信任/python/dnssec_chain.py` | 396 | 源 >300 |
| `02-网络安全/TLS-1.3握手/python/tls13_handshake.py` | 356 | 源 >300 |
| `02-网络安全/XDP包过滤/README.md` | 280 | README >200 |
| `02-网络安全/IKEv2-ESP/README.md` | 263 | README >200 |
| `02-网络安全/IKEv2-ESP/python/ikev2_demo.py` | 310 | 源 >300 |
| `02-网络安全/TLS-1.3握手/README.md` | 249 | README >200 |
| `02-网络安全/TLS-1.3握手/c/tls13_handshake.c` | 305 | 源 >300 |
| `02-网络安全/SYN-Cookie/README.md` | 229 | README >200 |
| `01-Web安全/SQL注入与预编译/README.md` | 227 | README >200 |
| `01-Web安全/JWT验证/README.md` | 226 | README >200 |
| `01-Web安全/Same-Origin与CORS/README.md` | 217 | README >200 |
| `01-Web安全/CSRF-Token/README.md` | 207 | README >200 |
| `01-Web安全/JWT验证/go/jwt_demo.go` | 314 | 源 >300 |
| `01-Web安全/CSRF-Token/python/csrf_demo.py` | 302 | 源 >300 |
| `01-密码学/非对称加密/RSA-PSS/README.md` | 204 | README >200 |