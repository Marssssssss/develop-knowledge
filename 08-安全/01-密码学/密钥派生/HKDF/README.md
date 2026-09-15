# HKDF: HMAC-based Extract-and-Expand Key Derivation Function(RFC 5869)

## 简介

HKDF 是 Krawczyk(EKE/TLS 之父)在 RFC 5869 定义的标准密钥派生函数, 是 **TLS 1.3、WireGuard、JSON Web Encryption(A256GCM 密钥派生)、Signal 协议**等的通用底座: 把一段"熵分布不均匀"的输入密钥材料 IKM(DH 共享密钥、主密钥)加工成任意长度、密码学强度的输出密钥材料 OKM。

本 demo 从零实现两阶段结构(Python 用 stdlib HMAC, C 从 SHA-256 开始自实现), 完整跑通 RFC 5869 附录 A.1-A.3/A.7 官方测试向量。

## 原理详解

### 1. 为什么需要"两阶段"

直接拿 `HMAC(IKM, info)` 派生密钥的问题:

1. **熵不一定均匀**: DH 群元素的分布有结构, 熵集中在高位;
2. **一次性**: 不好控制输出长度与多密钥隔离。

HKDF 拆成两步:

```
HKDF-Extract(salt, IKM)  = HMAC-Hash(salt, IKM)          -> PRK (HashLen 字节)
HKDF-Expand(PRK, info, L): T(1)=HMAC(PRK, info|0x01)
                           T(i)=HMAC(PRK, T(i-1)|info|i) -> OKM = (T(1)|T(2)|…)前 L 字节
```

- **Extract 把盐与 IKM 一起"浓缩"成固定长度伪随机密钥 PRK**——注意参数角色: salt 是 HMAC 的 key, IKM 是消息;
- **Expand 把 PRK "摊开"成 L 字节**, 输出上限 `L ≤ 255 × HashLen`(计数器只有 1 字节)。

### 2. 参数语义

| 参数 | 作用 | 缺省 |
| --- | --- | --- |
| salt | 防彩虹表/域分离, 非秘密 | HashLen 个 0x00 |
| IKM | 输入密钥材料(有熵即可, 可非均匀) | 无缺省 |
| info | 上下文绑定(协议名/方向/算法), 实现**多密钥隔离** | 空串 |
| L | 输出长度 | 无 |

### 3. 典型用法(TLS 1.3 的 HKDF 链)

```
early_secret = HKDF-Extract(salt=0,      IKM=PSK 或 0)
derived      = HKDF-Expand-Label(early_secret, "derived", "", H)
handshake_secret = HKDF-Extract(salt=derived, IKM=DHE_shared)
... 每层都用上一层输出作为下一层 salt —— "秘密树"
```

## 对比

| 维度 | HKDF | PBKDF2 | Argon2id |
| --- | --- | --- | --- |
| 输入熵假设 | 高熵(≥128 bit, 如 DH 输出) | 低熵口令 | 低熵口令 |
| 慢化/内存硬 | 无(快) | 迭代次数(仅 CPU) | 内存硬 |
| 抗 GPU 暴力破解 | 否(不需要) | 弱 | 强 |
| 多密钥隔离 | info 域分离 | 无 | 无 |
| 适用 | 协议密钥调度 | 口令→密钥(遗留) | 口令哈希(现代) |

## 环境

- Python 3.8+(hashlib/hmac 为 stdlib)
- C: C99; Go 1.20+

## 运行方式

```bash
python hkdf.py    # A.1/A.2/A.3/A.7 向量 + 域分离/雪崩/L 边界
go run hkdf.go    # 同上
cc hkdf.c -o hkdf && ./hkdf   # C 版含自实现 SHA-256+HMAC
```

## 关键代码

- `hkdf_extract()`: 3 行——空 salt 换成零字节, `HMAC(salt, IKM)`;
- `hkdf_expand()`: 反馈链 `T(i) = HMAC(PRK, T(i-1) | info | i)`, 计数器是**单个字节**;
- C 版自实现 HMAC(RFC 2104: `H((K⊕opad) | H((K⊕ipad) | m))`, B=64), SHA-256 尾块分 1~2 块填充。

## 性能边界

- Expand 每次 HMAC 处理 ≤ 64+HashLen+1 字节, 吞吐 ≈ HMAC 吞吐; C 版 > 100 MB/s;
- L 上限 8160 字节(SHA-256); 要更多就换 XOF(SHAKE)。

## 注意事项与常见坑

1. **Extract 的参数顺序最易写反**: salt 作 HMAC key, IKM 作消息; 写反在 A.1 向量上立刻现形, 但自造数据测不出来。
2. **缺省 salt 不是空字节**: 是 HashLen 个 0x00(A.7 专门给了向量)。
3. **info 为空 ≠ 不传 info**: 实现层面等价, 但协议层应总是显式绑定上下文, 否则不同用途的派生密钥相同。
4. **不要用 HKDF 派生口令**: IKM 必须高熵; 口令场景用 Argon2id/PBKDF2(见同目录 PBKDF2 demo)。
5. 截断 PRK 或跳过 Extract 直接把 IKM 当 PRK 用(当 len(IKM)==HashLen 时"能跑"), 会失去盐与浓缩, 多数协议禁止。

## 参考资料(实际读过)

1. RFC 5869(HKDF 规范 + 附录 A 全部测试向量): https://www.rfc-editor.org/rfc/rfc5869.html
2. RFC 2104(HMAC 定义, C 版自实现依据): https://www.rfc-editor.org/rfc/rfc2104.html
3. NIST SP 800-56C Rev2(Extract-then-Expand 的 NIST 表述): https://csrc.nist.gov/pubs/sp/800/56/c/r2/final
