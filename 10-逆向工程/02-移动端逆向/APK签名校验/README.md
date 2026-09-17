# APK 签名校验：v1 / v2 / v3

> APK 三代签名方案的结构与校验流程（官方规格全量落地）：v1 JAR 保护链、v2 全文件签名 + Signing Block + 分块摘要、v3 密钥轮换 proof-of-rotation。Python 15 项断言实跑全绿；Go 同构实现（人工审查 + 配平/append 检查）。

## 简介

- **v1（JAR 签名，Android 起始）**：`META-INF/MANIFEST.MF` 记录每个条目未压缩内容的摘要 → `.SF` 记录整个 MANIFEST 的摘要与逐节摘要 → `.(RSA|DSA|EC)` 是对 `.SF` 的 PKCS #7 签名。保护链：**签名 → .SF → MANIFEST.MF → 条目内容**，任何一环改动都断链。缺点：只保护 ZIP 条目、不覆盖中央目录，且验证需解压全部条目（慢）。
- **v2（Android 7.0）**：**全文件签名方案**。在「ZIP 条目内容」与「中央目录」之间插入 **APK Signing Block**（`uint64 size ×2 + magic "APK Sig Block 42"`，内含 ID-值对，v2 的 ID = `0x7109871a`）。摘要按**两级 Merkle**计算：每 1MB 块 `sha256(0xa5 ‖ uint32 len ‖ data)`，顶级 `sha256(0x5a ‖ uint32 count ‖ 拼接)`——分块是为了**并行**。**EOCD 的 CD 偏移字段口径**：签名块大小变化会改 CD 偏移，因此第 4 部分（EOCD）算摘要时该字段视为「签名分块偏移」。
- **v3（Android 9）**：在 v2 格式上（ID = `0xf05368c0`）加 `minSDK/maxSDK`（签名数据内外两份、必须一致）与 **proof-of-rotation**（附加属性 ID `0x3ba06f8c`）：**单链表**，最旧证书为根，每个节点的证书为下一节点背书签名；末级必须等于当前签名者证书。v3 排除多签名者与多祖先收敛。Android 13+ 的 `checkSignatures` 才正确识别 PoR。

## 原理详解（关键数字）

| 项 | 值 |
| --- | --- |
| Signing Block magic | `APK Sig Block 42`（16 字节） |
| v2 / v3 / PoR 分块 ID | `0x7109871a` / `0xf05368c0` / `0x3ba06f8c` |
| 摘要块前缀 / 顶级前缀 | `0xa5` / `0x5a` |
| 块大小 | 1MB（2^20），末块可短 |
| 签名算法 ID（0x0101） | RSASSA-PSS + SHA2-256（另有 0x0102 PSS-512、0x0103/0x0104 PKCS1、0x0201/0x0202 ECDSA、0x0301 DSA） |
| 防回滚 | `.SF` 主节 `X-Android-APK-Signed: 2`；有 v2 无该属性 → 拒绝降级为 v1 |

**验证顺序**（Android 9+）：先找 v3 → 回退 v2 → 回退 v1；v3/v2 失败**不得**再用 v1 验（防降级攻击，正是 Janus CVE-2017-13156 之后收紧的口径：v1-only APK 在头部拼接 dex 的攻击对 v2 无效，因为 v2 覆盖全部字节）。

## 对比

| | v1 | v2 | v3 |
| --- | --- | --- | --- |
| 保护范围 | ZIP 条目内容 | 全文件（除签名块自身） | 同 v2 + PoR |
| 中央目录/EOCD | 不保护 | 保护（EOCD 偏移替换口径） | 同 v2 |
| 验证速度 | 慢（逐条目解压） | 快（分块并行摘要） | 同 v2 |
| 密钥轮换 | 无 | 无 | 有（PoR 单链表） |
| 多签名者 | 支持 | 支持 | 不支持（Play 不发布多签名 v3） |

## 环境

`python` ≥3.8（标准库）；Go ≥1.21（可选，本机无工具链走人工审查）。

## 运行方式

```bash
python apk_signing.py   # 15 项断言
go run apk_signing.go   # 7 项同构断言(需 go 工具链)
```

## 关键代码

- `apk_digest()`：官方 EOCD 口径——`struct.pack_into("<I", eocd, 16, block_offset)` 后再拼入摘要（Go 侧 `binary.LittleEndian.PutUint32(e[16:], ...)`）。
- `chunk_digests()/top_digest()`：0xa5/0x5a 两级前缀，末块短于 1MB 时长度字段用实际长度。
- `verify_por()`：逐节点校验「上一级私钥为本级 signed data 背书」+ 末级证书 == 签名者证书。
- 键控摘要替代真实 RSA/ECDSA（聚焦结构，**口径声明**：算法层面非真实非对称签名）。

## 性能边界

- v2 摘要分块设计目标就是并行：1MB 块无依赖关系，可多线程/多核流水；v1 必须解压全部条目才能验完。
- 本 demo 的 1MiB 样本 = 1 块；`content2`（1MiB+500B）拆 2 块验证末块短长度口径。

## 注意事项与常见坑

- **EOCD 偏移字段不替换 → 摘要永远对不上**：插入签名块后 CD 实际偏移变了，但官方口径是按「签名分块偏移」取值（断言 8 专门验证此口径敏感性）。
- **防回滚属性写在 `.SF` 而不是 MANIFEST.MF**，且受 v1 签名保护——攻击者删不掉。
- **v3 的 minSDK/maxSDK 写两份**（signed data 内 + signer 层），校验必须比对一致（防范围裁剪攻击）。
- 模拟签名用键控摘要（`sha256("sig|"‖priv‖"|"‖data)`），**不能**当作真实签名安全模型；真实实现是 ASN.1 DER 的 RSASSA-PSS/ECDSA。
- v2 签名块里未知识别的 ID-值对**必须忽略**而不是报错（为 v3/v4 等扩展留空间，断言 5）。

## 参考资料（实际读过）

- [APK signature scheme v2 — Android Open Source Project](https://source.android.google.cn/docs/security/features/apksigning/v2)（source.android.com 不可达，以官方中国镜像为准）
- [APK signature scheme v3 — Android Open Source Project](https://source.android.google.cn/docs/security/features/apksigning/v3)
- [Janus（CVE-2017-13156）v2 签名分析 — shunix.com（第三方佐证 v2 全文件保护）](https://shunix.com/janus-two)
