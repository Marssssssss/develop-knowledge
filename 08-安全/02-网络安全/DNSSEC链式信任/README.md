# DNSSEC 链式信任 (RFC 4033 / RFC 4034 / RFC 4035)

## 简介

**DNSSEC**(Domain Name System Security Extensions,RFC 4033/4034/4035, 2005)是 DNS 的**对象级安全扩展**:给 DNS 数据加签名,让 **validating resolver** 能在解析过程中验证每个 RRset 真实来自该域 — **不加密查询**(那是 DoH/DoT 的职责)。

- **关键问题**:经典 DNS(1983 RFC 882/1033/1035)设计为"明文查询 + 无身份验证的目录",**任何中间人**都能伪造响应,导致 Kaminsky 攻击(CVE-2008-1447,**只用 10 秒钟就能毒化任意域名解析**)。运营商在 2008 年集体上 DNSSEC 的紧迫性由此而来。
- **关键概念**:
  - **信任链 (Chain of Trust)**:从根 KSK → TLD KSK → 子域 KSK → 子域 ZSK,每层用父域私钥签子域 DS,验证器从根 trust anchor 一路验证到底
  - **四类新 RR**:`DNSKEY`(公钥)、`RRSIG`(签名)、`DS`(Delegation Signer,父域签子域公钥的摘要)、`NSEC/NSEC3`(不存在证明)
  - **KSK 与 ZSK 分层**:
    - **KSK (Key Signing Key)**:签 DNSKEY RRset 自己,公开 hash 进 DS **→ 一两年轮换一次**
    - **ZSK (Zone Signing Key)**:签其他 RRset,**一两个月轮换一次**
    - 分层让 ZSK 轮换不影响 DS 链(DS 摘要会因 KSK 变化才改)
  - **Trust Anchor**:验证 resolver 内置的根 KSK 公钥 (2010 年 root zone signed,2018 KSK roll)
- **历史**:RFC 2065 1997 (DNSSEC 第 1 版,NSEC)、RFC 2535 1999 (修订)、RFC 4033/4034/4035 2005 (重写,引入 NSEC3 5175)、RFC 6605 ECDSA P-256/RFC 8080 Ed25519/Ed448、Root Zone 2010 signed、DNSSEC 2018 KSK 滚动(用 RFC 5011 trust anchor 自动更新)、Root 在 2024 年完成 P-256 (RFC 7583 DS algo)

## 原理详解

### Chain of Trust 链表结构

```
                  ROOT ZONE (root KSK)
                  ─────────────
  Resolver 自带 ROOT KSK 公钥 (Trust Anchor)
                  │
                  ▼  验 ROOT 的 RRSIG(.  DNSKEY RRset)
                  │  取出 . 的 KSK + ZSK
                  │
            ╔═══════════╗
            ║   .       ║  TLD  (.com, .org, ...的 ZSK 签 .com 的 DS)
            ║  KSK_top  ║
            ╚═══════════╝
                  │
                  ▼  验 . 的 RRSIG(DS RRset for example.com)
                  │  取出 DS(example.com) = hash(KSK_example.com)
                  │
       ╔═══════════════╗
       ║ example.com   ║
       ║ ZSK + KSK     ║
       ║ DS(in parent) ║
       ╚═══════════════╝
                  │
                  ▼  验 example.com 的 RRSIG(DNSKEY RRset)
                  │  取出 KSK_example.com 公钥
                  │
                  │  hash(KSK_example.com 公钥) == DS(example.com in .com)? ✓
                  │
                  ▼
       验 example.com 的 RRSIG(A example.com) with ZSK
                  ▼
       ✓ A=93.184.216.34 可靠
```

### DNSSEC 四种 RR 详解

#### 1. DNSKEY(RFC 4034 §2)

```
                  1   1   1   1   1   1
      0   1   2   3   4   5   6   7   8   9   0   1   2   3   4   5
    +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
    |  A/C  |  Z  |       Key Tag         |    Algorithm    |        |
    | (256/257)|(3) | (16 bits)           |    (8 bits)     |        |
    +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
    |                                                       /
    /                       Public Key                      /
    /                                                       /
```

| 字段 | 含义 |
| --- | --- |
| Flags (16 bit) | bit 7 = Z (bit 0),bit 8 = SEP (key-signing-key indicator) |
| Protocol (8 bit) | 必须 = 3 (DNSSEC) |
| Algorithm (8 bit) | 5 = RSASHA1, 7 = RSASHA1-NSEC3-SHA1, 8 = RSA/SHA-256, 10 = RSA/SHA-512, 13 = ECDSA P-256, 14 = ECDSA P-384, 15 = Ed25519, 16 = Ed448 |
| Public Key (变长) | 算法相关 |

**KSK vs ZSK**:通过 Flags bit 8 (SEP = Secure Entry Point) 区分。KSK 是 SEP=1,ZSK 是 SEP=0。DS 只 hash SEP=1 的公钥。

#### 2. RRSIG(RFC 4034 §3)

```
example.com.  IN  RRSIG  A  13 3 3600 (
                          20250901000000  ; Inception
                          20251201000000  ; Expiration
                          20250801000000  ; Signer's Key Tag (KSK)
                          example.com.     ; Signer's Name
                          <signature>      ; 算法相关
                          )
```

| 字段 | 大小 | 含义 |
| --- | --- | --- |
| Type Covered | 16 bit | 被签 RRset 的类型(A/AAAA/MX/DNSKEY 等) |
| Algorithm | 8 bit | 同 DNSKEY.Algorithm |
| Labels | 8 bit | owner name 标签数(去掉末尾 0 长度后) → 防 wildcard manipulation |
| Original TTL | 32 bit | 原始 RRset TTL(验证用它,不是当前缓存 TTL) |
| Signer Name | 变长 | 签名 key 的 owner(通常是 apex) |
| Key Tag | 16 bit | 匹配 DNSKEY 的 key tag,快速定位 |

#### 3. DS(Delegation Signer, RFC 4034 §5)

```
com.  IN  DS  12345 13 2  4F14C5A1B8C9... ; DS12345 SHA-256(KSK example.com 公钥)
```

| 字段 | 大小 | 含义 |
| --- | --- | --- |
| Key Tag | 16 bit | 匹配的 DNSKEY 的 key tag |
| Algorithm | 8 bit | 必须 = DNSKEY.Algorithm |
| Digest Type | 8 bit | 1 = SHA-1,2 = SHA-256 (BCP),3 = GOST,4 = SHA-384 |
| Digest | 变长 | Digest Type 函数的输出 |

#### 4. NSEC / NSEC3(不存在证明, RFC 4034 §4 / RFC 5155)

```
NSEC:   name1.example.com.  NSEC  name2.example.com. A NS SOA RRSIG ...

NSEC3:  HT(0qp...).example.com. NSEC3 1 0 10 2F9B... (
        2PGLM9DMD1NA7L8H8I4P76...   ; 下一 hash
        A NS SOA RRSIG
        )
```

| NSEC | NSEC3 |
| --- | --- |
| 列下一域名 (canonical name) | 列下一**hash**(SHA-1) |
| Type Bit Map 列出该 name 有的 RR 类型 | 同左 |
| 易 zone walking(顺序遍历域所有名字) | 防 zone walking(只暴露 hash,不知道真域名) |

Zone Walk:用 NSEC 串起来 → get 整个 zone 所有 name,枚举出子域名。RFC 5155 NSEC3 用 hash 替代 name,无法直接遍历。

### 验证算法(RFC 4035)

```
验证器收到 example.com IN A 1.2.3.4 + RRSIG example.com IN RRSIG A ... :
  1. RRSIG.Inception ≤ now ≤ RRSIG.Expiration  (not expired)
  2. 用 signer name (e.g. "example.com.") 查 DNSKEY 集合
  3. 用 signer key tag 定位具体 ZSK(可能多个)
  4. RRset canonical sort + RRSIG.signature 验证
  5. 找父 zone(.com) DS for example.com,hash(KSK example.com) == DS.Digest?
  6. 验证 .com DNSKEY 集合合法 + .com RRSIG(DS) 验证 ...
  7. 一直追溯到根 trust anchor (根 KSK)
  8. 全链合法 → Secure,否则 Bogus (要回 SERVFAIL 给客户端)
```

### 验证器最终状态(RFC 4035 §4.3 + RFC 7719)

| 状态 | 含义 | resolver 行为 |
| --- | --- | --- |
| Secure | 链完整、签名合法 | 返回 AD=1 |
| Insecure | 该 zone 在可信链的某处被父域声明**未签**(DS 不存在 + NSEC 证明) | 返回 AD=0,但**不**报警 |
| Bogus | 应该能验证但失败(过期、改动、缺 RRSIG) | **返回 SERVFAIL**(可选 STRICT/RFC 7645) |
| Indeterminate | resolver 没有任何 trust anchor 涵盖该 zone | 跳过验证,正常返回 |

### RRset Canonicalization

所有 DNSSEC 验证前要做 RRset canonical sort + name canonicalize(全转为小写 + wire format),否则同一 RRset 不同文本表示会算成不同。

## 环境准备

- 操作系统:任意(Linux/macOS/Windows)
- 语言版本:
  - Python 3.7+
  - Go 1.20+
  - C + OpenSSL(RSA/ECDSA/Ed25519 签名)
- 依赖:`dnspython`(`pip install dnspython`)— 用于解析真实 DNS 协议的 RRset wire format
- 实际验证 DNSSEC 链:可以使用 `dig +dnssec example.com` 或 `python -m dns.resolver.Resolver(...)`

## 运行方式

```bash
# Python
pip install dnspython cryptography
python3 python/dnssec_chain.py

# Go
cd go && go run dnssec_chain.go

# C
gcc -O2 -Wall -Wextra c/dnssec_chain.c -lcrypto -o dnssec
./dnssec
```

输出:
1. 模拟"zone sign"流程:为 example.com 创建 ZSK,签 A RRset,生成 RRSIG
2. 模拟 KSK:ZSK 创建 DNSKEY RRset,KSK 签 DNSKEY RRset,生成 RRSIG
3. 模拟父 zone:计算 DS = SHA-256(KSK example.com 公钥 wire format)
4. 模拟 resolver 验证:从根 trust anchor 开始,逐层验证,直到 example.com 的 A 记录 + RRSIG,标记 Secure/Insecure/Bogus

## 关键代码片段

完整实现见 `python/dnssec_chain.py`。核心算法:

```python
import hashlib, hmac
from cryptography.hazmat.primitives.asymmetric import ed25519, ec
from cryptography.hazmat.primitives import hashes

# 1. 生成 ZSK + KSK (Ed25519 演示最简短)
zsk_priv = ed25519.Ed25519PrivateKey.generate()
zsk_pub = zsk_priv.public_key()
ksk_priv = ed25519.Ed25519PrivateKey.generate()
ksk_pub = ksk_priv.public_key()

# 2. KSK 签 DNSKEY RRset (自己签)
dnskey_rrset_bytes = canonical_encode_dnskey(zsk_pub, ksk_pub)
rdata_rrset_dnskey = encode_rrset_canonical(rrset_dnskey_pre)
ksk_signature = ksk_priv.sign(canonical_form(dnskey_rrset_bytes))

# 3. ZSK 签 A RRset
a_rrset = RRset(name='example.com.', rdata={'A': '1.2.3.4'})
a_canonical = canonical_form(a_rrset)
zsk_signature = zsk_priv.sign(a_canonical)

# 4. 父域算 DS = SHA-256(KSK 公钥 DER)
ds_digest = hashlib.sha256(ksk_pub.public_bytes_raw()).digest()

# 5. Resolver 验证
def verify(name, rrset, rrsig, dnskey_set):
    rrset_canonical = canonicalize_rrset(rrset, rrsig['Signer's Name'])
    try:
        zsk_key = next(k for k in dnskey_set if k['key_tag'] == rrsig['Key Tag'])
        zsk_key['pub_obj'].verify(rrsig['Signature'], rrset_canonical)
        return 'Secure'
    except InvalidSignature:
        return 'Bogus'

# 6. 验证完整链(根 KSK 公钥作 trust anchor)
def walk_chain(root_pub, target):
    curr = 'root';   # root zone (.)
    chain = []
    while curr:
        dnskey_set = fetch(curr, 'DNSKEY')
        ds_set      = fetch(parent(curr), 'DS')
        if not ds_set:
            return 'Insecure'   # NSEC3 证明子区未签
        for ds in ds_set:
            actual_digest = hashlib.sha256(get_dnskey_for_tag(dnskey_set, ds['Key Tag'])).digest()
            if actual_digest == ds['Digest']:
                chain.append((curr, 'Secure'))
                curr = next_subzone(curr)
                break
        else:
            return 'Bogus'
    return 'Secure'
```

## 性能与边界

- **Ed25519 签名/验证**:~50 µs / 30 µs(短消息)
- **RSA-2048**:签名 ~2 ms,验证 ~0.2 ms
- **ECDSA P-256**:签名 ~0.4 ms,验证 ~0.5 ms
- **缓存**:RRset 在 TLL 内可缓存;RRSIG 含 inception/expiration,提前 1/4 TTL refresh
- **算法选用**:`RSA/SHA-256` (algo 8) — 老但兼容;`ECDSA P-256` (algo 13) — 主流;`Ed25519` (algo 15) — 简洁快速;`Ed448` (algo 16) — 大尺寸;新算法 RSASHA256-EMSA-PSS(50)抗量子候选
- **不支持的算法**:RSAMD5/1、DH/2、DSA/3 → 标记 Insecure,不阻断
- **签名过期窗口**:Inception ≤ now + 5 min ≤ Expiration - 5 min(吸收时钟偏移)
- **ZSK 轮换**:典型 30-90 天;KSK 轮换 (RFC 7583 RFC 6781) 需先 standby KSK 双签期,然后撤销旧 KSK,典型一年

## 注意事项与常见坑

- **CNAME 跨域**:CNAME 链中每个 CNAME RRset 都要有 RRSIG,且目标也要有 RRSIG,否则链是 Bogus。Wildcard 也要 NSEC3 proof covering record。
- **NSEC3 缺失 / 循环**:zone sign 时 NSEC3 hash 必须单调链式闭环,否则 Bogus。
- **Algorithm 5 (RSASHA1)**:很多验证器已拒绝(因为 SHA-1 碰撞);signer 切到 algo 8/13/15。
- **DS 缺失 vs NSEC3 DS-EXISTS 不存在**:父域须有 NSEC3 证明 "example.com 子域不存在 DS 记录" → resolver 标记 Insecure,否则整个 zone Bogus。
- **DS shift bug**:KSK 轮换时若父域 DS hash 出错 → 全 zone Bogus(历史上 Comodo、GoDaddy 都犯过)。
- **Clock skew**:验证器时钟未来/过去偏差大 → Bogus;NTP 必须准(±5 分钟容忍)。
- **Wildcard 验证**:wildcard.example.com 解析到的 RRSIG 须是 \* 的 RRset 签的,且 NSEC3 须证明 wildcard 与原 query name 之间没有更精确匹配。
- **Local trust anchor + RFC 5011**:DNSSEC 允许 trust anchor 自动更新,需 keep new KSK 至少 30 天(hold-down)。
- **Negative Trust Anchors (NTA)**:用户可手工声明"某 zone 永远 Insecure",典型用途:内部测试 zone 当 broken 时先 NTA,等修好去掉。
- **DoH/DoT 与 DNSSEC 分离**:DNSSEC 验证 RRset 完整性,DoH/DoT 加密 channel;两者独立。

## 对比 / 选型

| 方案 | 解决 | 优 | 劣 |
| --- | --- | --- | --- |
| **DNSSEC** | RRset 真实 + 完整 | 标准化,deployed(根 + .com + .org 已签) | 不加密;验证失败整 SERVFAIL;DS 轮换繁琐 |
| **DoH / DoT** | 加密 DNS 查询 | 防止 ISP 偷看查询 | 不解决伪造响应 |
| **DNSCurve (NaCl)** | 加密 + 验证 | 单 key,轻量 | 没广泛部署 |
| **Cached-Only resolver + DoH** | ISP 不偷看 + 验证(经 upstream) | 推荐给个人 | 上游是关键信任点 |
| **Recursive resolver 验证** | 标准做法 | 终端不需配置 | 需 ISP/谷歌/Cloudflare 提供 valid resolver |

实际企业部署:
1. 上游用 Cloudflare 1.1.1.1 或 Quad9 9.9.9.9(都 enable DNSSEC validation)
2. 个人配置 systemd-resolved 或 unbound,设 trust anchor 为 [内置根 KSK]
3. DoH 上游为加密备份

## 参考资料(实际阅读过的权威来源)

- [RFC 4033 — DNS Security Introduction and Requirements](https://datatracker.ietf.org/doc/draft-ietf-dnsext-dnssec-intro/12) — DNSSEC 总览,术语,Chain of Trust 形式定义 `DNSKEY->[DS->DNSKEY]*->RRset`,new RR types 用途
- [RFC 4034 — Resource Records for the DNS Security Extensions](https://www.rfc-editor.org/rfc/rfc4034.html) — DNSKEY / RRSIG / DS / NSEC 完整 wire format(wire format 字段大小写对齐)
- [RFC 4035 — Protocol Modifications for the DNS Security Extensions](https://www.rfc-editor.org/rfc/rfc4035.html) — 验证算法步骤、四个 resolver 状态(Secure/Insecure/Bogus/Indeterminate)、canonical sort
- [RFC 5155 — DNS Security (DNSSEC) Hashed Authenticated Denial of Existence](https://www.rfc-editor.org/rfc/rfc5155.html) — NSEC3 抗 zone walking,SHA-1 hash + 盐值 + 迭代次数
- [RFC 7583 — DNSSEC Algorithm on the Root Zone](https://www.rfc-editor.org/rfc/rfc7583.html) — 根 KSK 算法 ECDSA P-256 决策
- [RFC 5011 — Automated Updates of DNS Security (DNSSEC) Trust Anchors](https://www.rfc-editor.org/rfc/rfc5011.html) — resolver 自动更新 trust anchor 协议(根 KSK 滚动机制)
- [RFC 6605 — Elliptic Curve Digital Signatures (ECDSA) for DNSSEC](https://www.rfc-editor.org/rfc/rfc6605.html) — algo 13/14 = ECDSA P-256/P-384
- [RFC 8080 — Edwards-Curve Digital Security Algorithm (EdDSA) for DNSSEC](https://www.rfc-editor.org/rfc/rfc8080.html) — algo 15 = Ed25519
- [CDN Certified — DNSSEC (DNS Security Extensions)](https://cdncertified.com/glossary/dnssec) — 4 RR 类型 + ZSK/KSK + Chain of Trust + 验证器 4 状态汇总
- [Captain DNS — Chain of Trust Explained in 5 Minutes](https://www.captaindns.com/en/blog/dnssec-chain-of-trust-explained) — 多层 zone sign 完整示例,DS shift bug 案例
