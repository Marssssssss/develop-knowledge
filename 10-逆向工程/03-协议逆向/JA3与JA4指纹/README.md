# JA3 与 JA4：TLS ClientHello 握手指纹

> 加密流量分析的核心武器：TLS 握手在明文中完成，**握手参数本身就成了客户端指纹**。JA3（Salesforce 2017）把 ClientHello 五字段串接后取 MD5；JA4（FoxIO 2023）改为"可读 a 段 + 两段截断 SHA-256"，排序抗扩展顺序随机化。本 demo 从零实现 ClientHello 解析 + 两套指纹计算，并用两个**官方已发布指纹值**做端到端回环验证。

## 一、简介

- **JA3**：`SSLVersion,Cipher,SSLExtension,EllipticCurve,EllipticCurvePointFormat` 五个字段的十进制值，字段内 `-` 连接、字段间 `,` 连接 → MD5（32 字符）。对 GREASE 值忽略。JA3S 是服务端版（只取三字段）。JA3+JA3S 组合可指纹化整个密码学协商——即使 IP/端口/证书不断变化，客户端应用指纹不变（Tor `e7d705…`、Trickbot `6734f3…`、Emotet `4d7a28…` 恒定）。
- **JA4**：`a_b_c` 三段式。a 段 10 字符可读；b/c 段各为截断 SHA-256（12 字符）。设计动机（FoxIO Q&A）：① 密码套件与扩展**排序**——对抗 cipher stunting 与 Chromium 扩展顺序随机化；② 加入签名算法保持排序后唯一性；③ 三段可局部匹配（`JA4_ac` 只看 a+c，可跨 b 段漂移追踪同一行为体——GreyNoise 扫描者案例）。
- 维护状态：salesforce/ja3 仓库 2025-05 归档，维护权移交 FoxIO 的 JA4。

## 二、原理详解

### 2.1 ClientHello 结构（RFC 8446 §4.1.2）

```
Record:      type(0x16) | version(2) | length(2)
Handshake:   type(0x01) | length(3)
ClientHello: legacy_version(2) | random(32) | session_id(1+n)
             | cipher_suites(2+n×2) | compression(1+n)
             | extensions(2+n):
                 type(2) | len(2) | data
   0x0000 server_name        0x000a supported_groups(曲线)
   0x000b ec_point_formats   0x000d signature_algorithms
   0x0010 ALPN               0x002b supported_versions
```

### 2.2 JA3 计算

```
串 = "769,47-53-5-10-...-4,0-10-11,23-24-25,0"
      │   │            │   │      │     └ point formats (ext 0x000b)
      │   │            │   │      └ 椭圆曲线 (ext 0x000a)
      │   │            │   └ 扩展类型列表 (含 SNI=0, wire 顺序)
      │   └ 密码套件 (wire 顺序)
      └ SSLVersion = legacy_version 十进制 (0x0301=769)
JA3 = MD5(串)
```

字段无值时留空（如无扩展 → `769,4-5-10,,,`）。GREASE 值全部忽略——GREASE（RFC 8701）是 Google 为防"实现只认已知值"而生造的哑值 `0x?a0a`（高字节低半字节=0xa 且两字节相同，即 0x0a0a/0x1a1a/…/0xfafa）。

### 2.3 JA4 计算

```
a 段(10字符): [t|q] + 版本2 + [d|i] + 套件数2 + 扩展数2 + ALPN前2字符
    版本: supported_versions 扩展里的最高值 (不是 legacy 字段!)  13/12/11/10
    d/i: 有无 SNI     套件数: 剔 GREASE     扩展数: 剔 GREASE (SNI/ALPN 仍计数)
    ALPN 无 → "00"
b 段(12字符): SHA-256("002f,0035,...")[:12]   套件 %04x 排序, 剔 GREASE; 空 → "000000000000"
c 段(12字符): SHA-256("0005,...,ff01_0403,...")[:12]
              扩展 %04x 排序, 剔 GREASE+0000(SNI)+0010(ALPN); '_' 后接 wire 顺序签名算法
```

**边界**：计数 2 位十进制前导零填充；空列表输出 `000000000000`；JA4 全部小写 hex。

### 2.4 官方向量（本 demo 的回环断言）

- **JA4**：`t13d1516h2_8daaf6152771_e5627efa2ab1`（FoxIO 规范例）——15 套件、16 扩展、ALPN=h2。
- **JA3**：`769,47-53-5-10-49161-49162-49171-49172-50-56-19-4,0-10-11,23-24-25,0` → `ada70206e40642a3e4461f35503241d5`（salesforce/ja3 README 例）。

## 三、JA3 vs JA4 对比

| | JA3 | JA4 |
| --- | --- | --- |
| 哈希 | MD5 全长 32 字符 | SHA-256 截断 2×12 字符 |
| 列表顺序 | wire 顺序（顺序敏感） | 排序（抗随机化） |
| GREASE | 忽略 | 忽略 |
| 可读性 | 无 | a 段直接可读（t13d1516h2） |
| 局部匹配 | 无 | a/b/c 可任意组合（`JA4_ac` 等） |
| 版本来源 | legacy 字段 | supported_versions 最高值 |
| 签名算法 | 不含 | c 段含（保唯一性） |
| 脆弱性 | uTLS 伪装、扩展乱序即变 | 仍可被完整 ClientHello 模仿库仿造 |

## 四、环境与运行

- Python ≥3.8（仅标准库）：`python tls_fingerprint.py`
- Go ≥1.18：`go run .`（解析/构造在 chello.go，指纹计算在 tls_fingerprint.go）
- 输出：两条合成 ClientHello 的字段解析、JA3/JA4 全串与哈希、与官方向量比对结果。

## 五、关键代码

```python
def is_grease(v): return (v & 0x0f0f) == 0x0a0a      # 0x0a0a, 0x1a1a, ... 0xfafa

def ja4_a(ch):
    ver = max((v for v in ch.supported_versions if not is_grease(v)),
              default=ch.legacy_version)
    return ('t' + VER_CODE[ver] + ('d' if ch.sni else 'i')
            + f'{len(ch.ciphers):02d}'
            + f'{len([e for e in ch.exts if not is_grease(e)]):02d}'
            + (ch.alpn_first[:2] or '00'))
```

## 六、性能边界

- 解析 O(报文长)，指纹 O(1) 哈希；百万级 pcaps 可实时计算。
- MD5/SHA-256 均不构成"安全"语义——指纹只做聚类，不做身份认证；不同客户端可共享 TLS 栈实现（Go 程序 b 段普遍相同：Sliver 与 Evilginx 同为 `9dc949149365`）。
- JA4 随 TLS 库升级漂移（约一年一变），不能假设环境指纹恒定。

## 七、注意事项与常见坑

1. **版本字段别取错**：JA4 的版本来自 supported_versions 扩展（0x002b）的最高非 GREASE 值，legacy_version 字段在 TLS 1.3 客户端里恒为 0x0301，取错整段 a 就错。
2. **扩展计数 ≠ 进哈希的扩展数**：a 段计数含 SNI/ALPN（只剔 GREASE），c 段哈希要再剔 SNI(0x0000)/ALPN(0x0010)——FoxIO 规范例里"16 计数、14 进哈希"。
3. **签名算法不排序**：套件与扩展排序、sig algs 保持 wire 顺序（顺序本身携带信息）。
4. **GREASE 判定用位运算**`(v & 0x0f0f) == 0x0a0a`，别列白名单——GREASE 值会随版本扩充。
5. JA3 的扩展列表含 SNI(0)：salesforce 例 `0-10-11` 里第一个 0 就是 server_name。
6. 多个 server_name / ALPN 条目时取第一个；JA4 的 ALPN 字符是"首个 ALPN 值的前两个字符"（`h2`/`h3`），不是首尾字符——JA4S 才是首尾字符。

## 八、参考资料（实际读过）

- [JA3 - A method for profiling SSL/TLS Clients — salesforce/ja3（GitHub）](https://github.com/salesforce/ja3) —— 五字段定义与连接规则、完整示例串与 MD5、空字段约定、GREASE 忽略、JA3S 三字段、Tor/Trickbot/Emotet 恒定指纹、2025-05 归档与移交 FoxIO 公告
- [FoxIO-LLC/ja4（GitHub README）](https://github.com/FoxIO-LLC/ja4) —— a_b_c 三段式与局部匹配设计（GreyNoise `JA4_ac` 案例）、排序动机（cipher stunting、Chromium 扩展乱序）、签名算法保唯一性、规范例 `t13d1516h2_8daaf6152771_e5627efa2ab1`、JA4S 示例（IcedID/Sliver/SoftEther）、许可（JA4 BSD-3 / JA4S FoxIO License 1.1）
- [What does each field in a JA4 fingerprint mean? — Trueguard](https://trueguard.io/knowledgebase/what-is-ja4-and-ja4t-fingerprints) —— a 段六字段逐位解析（含"版本取 supported_versions 而非包头顶部字段"、"计数含 SCSV/实验段、剔 GREASE"、"计数是 wire 数不是进哈希数"）、b/c 段排序与截断细节、`-r`/`-o` 调试模式、空列表 `000000000000`
- [TLS Fingerprinting — NETCAP docs](https://docs.netcap.io/master/tls-fingerprinting) —— JA4/JA4S 格式速查（含 JA4S 七字符 a 段、c 段扩展不排序）、JA4 相对 JA3 的优势清单（抗乱序、可读、QUIC、含 ALPN）
