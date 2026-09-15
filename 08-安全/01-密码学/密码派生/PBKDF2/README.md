# PBKDF2: Password-Based Key Derivation Function 2(RFC 8018 / RFC 6070 向量)

## 简介

PBKDF2 是 PKCS #5(RFC 8018)定义的口令密钥派生函数: 把人类可记的低熵口令加工成密钥(或口令哈希), 通过**大量迭代 HMAC** 放大每次猜测的成本。它是 Wi-Fi WPA2(WPA-Enterprise 与个人模式均用)、加密 zip、Apple/iOS 旧版备份、许多遗留系统的实际底座; 在口令哈希赛道上已被 Argon2id 超越(见同目录 Argon2id demo), 但作为标准与向后兼容的存量巨大。

本 demo 从零实现 F 函数迭代链(Python 侧 HMAC 用 stdlib, C 连 SHA-1 自实现), 完整命中 RFC 6070 全部 6 组官方向量(含 NUL 字节陷阱用例)。

## 原理详解

### 1. 算法结构(RFC 8018 §5.2)

```
DK = PBKDF2(P, S, c, dkLen)
每个输出块 i = 1..ceil(dkLen/hLen):
  F(P, S, c, i):
    U1 = HMAC-SHA1(P, S || INT(i))     ← INT(i) 是大端 4 字节块号
    U2 = HMAC-SHA1(P, U1)
    ...
    F  = U1 ⊕ U2 ⊕ ... ⊕ Uc           ← c 次迭代, 全部异或
DK = F(1) || F(2) || ... 截断到 dkLen
```

- **迭代 c**: 每次口令猜测成本 ×c。RFC 6070 向量 c=1~4096(演示用), 现代推荐 60 万~数百万人次(OWASP 2023 建议 PBKDF2-HMAC-SHA256 600k);
- **盐 S**: 每用户唯一随机(≥8 字节), 让同一口令派生出不同密钥, 消灭彩虹表预计算;
- **异或而非串联**: U 间 XOR 让攻击者无法用时空折中(memory-time tradeoff)跳步——任意一步都不能省。

### 2. 为什么"慢"是特性

口令熵低(常见口令 ~20 bit), 防线只有成本: 攻击者每秒尝试次数从 10^9(裸 SHA-1)降到 10^2~10^3(百万次迭代)。PBKDF2 的慢是**CPU 串行慢**——GPU/FPGA 可高度并行, 这是它相对 Argon2 的核心劣势(后者内存硬)。

### 3. 与 Argon2id 对比

| 维度 | PBKDF2 | Argon2id |
| --- | --- | --- |
| 抗 GPU | 弱(纯 CPU 迭代) | 强(内存硬, GiB 级) |
| 抗时空折中 | 中 | 强(数据依赖访存) |
| 标准化 | RFC 8018, 到处都有 | PHC 冠军, RFC 9106 |
| 调参 | c(迭代) | m(内存)/t(时间)/p(并行) |
| 适用 | 兼容存量/WPA2 | 新系统默认 |

## 环境

- Python 3.8+(hashlib/hmac stdlib, hashlib 同时用于对照); C99; Go 1.20+

## 运行方式

```bash
python pbkdf2.py    # RFC 6070 全向量 + 迭代/口令/盐敏感性
go run pbkdf2.go
cc pbkdf2.c -o pbkdf2 && ./pbkdf2    # C 版自含 SHA-1+HMAC
```

## 关键代码

- `pbkdf2()` 核心三行: `U1 = HMAC(P, S||INT(i))` → 循环 `U = HMAC(P, U)` 累积 XOR → 按块拼接截断;
- INT(i) 必须**大端 4 字节**(块号从 1 起), 这是与 HKDF 单字节计数器最易混淆的点;
- C 版 `hmac_sha1` 分配 `max(mlen,20)+64` 缓冲, 因为 U 迭代时会先写后读同一缓冲。

## 性能边界

- Python 版 4096 次迭代 ~10 ms(演示量级); 百万次建议 C(~1 s @ SHA-1)或直接 Argon2;
- SHA-1 单核 ~200 MB/s, 迭代开销 ≈ c × 2 次 HMAC 块压缩。

## 注意事项与常见坑

1. **RFC 6070 的 "\\0" 用例**: 口令/盐含 NUL 字节, 语言层用 C 字符串会截断——Python `b"pass\0word"` 没问题, C 必须 `{'p','a','s','s',0,...}` 显式长度。
2. **dkLen 跨块**: >20 字节要多块拼接, 块号 INT(i) 别写成字节 0x01(HKDF 是 1 字节, PBKDF2 是 4 字节, 两者不要记混)。
3. **盐要随机且唯一**, 只加常数盐=没有盐; 别把盐当秘密(它和哈希一起存)。
4. **迭代数必须随硬件升级上调**, 固定 c=4096 在 2026 年是不合格的(OWASP 建议 SHA-256 600k 起)。
5. 校验派生结果要**常时比较**(`hmac.compare_digest`), 逐字节短路比较泄露前缀信息。

## 参考资料(实际读过)

1. RFC 6070(PBKDF2-HMAC-SHA1 全部 6 组测试向量, 含 NUL 用例): https://www.rfc-editor.org/rfc/rfc6070.html
2. RFC 8018(PKCS #5 v2.1, PBKDF2 规范与盐/迭代建议): https://www.rfc-editor.org/rfc/rfc8018.html
3. RFC 2104(HMAC, C 版基件): https://www.rfc-editor.org/rfc/rfc2104.html
4. OWASP Password Storage Cheat Sheet(现代迭代次数建议, 对比参考): https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
